#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File : language_model_manager.py
@Time : 2026/07/29
@Author : Youyou
"""

import os
import math
from functools import lru_cache
from typing import Any, Mapping, Optional, Type

import yaml
from injector import inject, singleton
from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel, Field, ValidationError, model_validator

from internal.exception.exception import NotFoundException, ValidateErrorException
from .entities.provider_entity import Provider, ProviderEntity
from .entities.model_entity import (
    BaseLanguageModel,
    LanguageModelConfig,
    ModelEntity,
    ModelParameter,
    ModelParameterType,
    ModelType,
    StructuredOutputStrategy,
)

FORBIDDEN_MODEL_PARAMETERS = {
    "api_key",
    "openai_api_key",
    "api_base",
    "base_url",
    "openai_api_base",
    "model",
    "model_name",
}


@inject
@singleton
class LanguageModelManager(BaseModel):
    """语言模型管理器"""

    provider_map: dict[str, Provider] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_language_model_manager(self) -> dict[str, Any]:
        """预设规则校验 对管理器进行初始化"""
        current_path = os.path.abspath(__file__)
        providers_path = os.path.join(os.path.dirname(current_path), "providers")
        providers_yaml_path = os.path.join(providers_path, "providers.yaml")

        # 读取 providers.yaml 获取提供商列表
        with open(providers_yaml_path, encoding="utf-8") as f:
            providers_yaml_data = yaml.safe_load(f)

        self.provider_map = {}
        for index, provider_yaml_data in enumerate(providers_yaml_data):
            # 构建提供商实体
            provider_entity = ProviderEntity(**provider_yaml_data)
            self.provider_map[provider_entity.name] = Provider(
                name=provider_entity.name,
                position=index + 1,
                provider_entity=provider_entity,
            )

        return self

    def get_provider(self, provider_name: str) -> Optional[Provider]:
        """根据提供商名称获取提供商"""
        provider = self.provider_map.get(provider_name, None)
        if provider is None:
            raise NotFoundException("该提供商不存在!")
        return provider

    def get_providers(self) -> list[Provider]:
        """获取所有提供者列表信息"""
        return list(self.provider_map.values())

    def get_visible_providers(self) -> list[Provider]:
        """获取模型列表接口可展示的提供者。"""
        return [
            provider
            for provider in self.provider_map.values()
            if provider.provider_entity.visible
        ]

    def get_model_class_by_provider_and_type(
        self,
        provider_name: str,
        model_type: ModelType,
    ) -> Optional[Type[BaseLanguageModel]]:
        """根据提供商名称+模型类型，获取模型类"""
        provider = self.get_provider(provider_name)

        return provider.get_model_class(model_type)

    def get_model_class_by_provider_and_model(
        self,
        provider_name: str,
        model_name: str,
    ) -> Optional[Type[BaseLanguageModel]]:
        """根据传递的提供者名字+模型名字获取模型类"""
        # 1.根据名字获取提供者信息
        provider = self.get_provider(provider_name)

        # 2.在提供者下获取该模型实体
        model_entity = provider.get_model_entity(model_name)

        return provider.get_model_class(model_entity.model_type)

    def validate_model_config(
        self, model_config: Mapping[str, Any] | LanguageModelConfig
    ) -> LanguageModelConfig:
        """校验模型选择与参数，并补齐模型目录中声明的默认参数。"""
        try:
            config = (
                model_config
                if isinstance(model_config, LanguageModelConfig)
                else LanguageModelConfig.model_validate(model_config)
            )
        except Exception as exc:
            raise ValidateErrorException("模型配置格式错误") from exc

        try:
            provider = self.get_provider(config.provider)
            model_entity = provider.get_model_entity(config.model)
        except NotFoundException as exc:
            raise ValidateErrorException(exc.message) from exc
        if model_entity.model_type != ModelType.CHAT:
            raise ValidateErrorException("当前仅支持对话模型")

        parameter_map = {
            parameter.name: parameter for parameter in model_entity.parameters
        }
        validated_parameters: dict[str, Any] = {}
        supplied_names = set(config.parameters)

        for name, value in config.parameters.items():
            if name in FORBIDDEN_MODEL_PARAMETERS:
                raise ValidateErrorException(f"模型参数{name}不允许覆盖")

            rule_name = name
            if name == "max_tokens" and name not in parameter_map:
                rule_name = "max_tokens"
            rule = parameter_map.get(rule_name)
            if rule is None:
                raise ValidateErrorException(f"模型参数{name}不存在")
            self._validate_parameter_value(name, value, rule)
            validated_parameters[name] = value

        for name, rule in parameter_map.items():
            compatible_name = "max_tokens" if name == "max_tokens" else name
            if name not in supplied_names and compatible_name not in supplied_names:
                if rule.default is not None:
                    validated_parameters[name] = rule.default

        return config.model_copy(update={"parameters": validated_parameters})

    # 校验以及默认值
    def create_language_model(
        self, model_config: Mapping[str, Any] | LanguageModelConfig
    ) -> BaseLanguageModel:
        """依据统一配置创建 Provider 对应的 LangChain 对话模型。"""
        config = self.validate_model_config(model_config)
        provider = self.get_provider(config.provider)
        model_entity = provider.get_model_entity(config.model)
        model_class = provider.get_model_class(ModelType.CHAT)
        init_kwargs = {
            **model_entity.attributes,
            **config.parameters,
            "features": model_entity.features,
            "metadata": {
                **model_entity.metadata,
                "provider": config.provider,
                "model": config.model,
            },
        }
        provider_entity = provider.provider_entity

        if provider_entity.api_key_env:
            api_key = os.getenv(provider_entity.api_key_env)
            if not api_key:
                raise ValidateErrorException(
                    f"缺少环境变量{provider_entity.api_key_env}，无法调用{provider_entity.label}"
                )
            init_kwargs["api_key"] = api_key

        base_url = (
            os.getenv(provider_entity.base_url_env)
            if provider_entity.base_url_env
            else None
        ) or provider_entity.base_url
        if base_url:
            init_kwargs["base_url"] = base_url

        # 只在 SDK 确实支持的字段上配置传输边界，不注入模型生成参数。
        if config.provider in {"openai", "deepseek", "moonshot", "doubao", "zhipu"}:
            init_kwargs.update(timeout=60, max_retries=0)
        elif config.provider == "ollama":
            init_kwargs["client_kwargs"] = {"timeout": 60}

        try:
            return model_class(**init_kwargs)
        except ImportError:
            raise ValidateErrorException(
                f"模型提供者{config.provider}缺少可选SDK依赖"
            ) from None

    def get_default_model_entity(self) -> ModelEntity:
        """读取默认模型目录元数据，不创建 SDK 客户端或发起模型调用。"""
        config = self._get_system_model_config()
        return self.get_provider(config["provider"]).get_model_entity(config["model"])

    def create_default_language_model(
        self, parameters: Optional[Mapping[str, Any]] = None
    ) -> BaseLanguageModel:
        """创建不隶属于特定应用的系统辅助模型。"""
        return self.create_language_model(self._get_system_model_config(parameters))

    def create_structured_chat_model(
        self,
        model_config: Mapping[str, Any] | LanguageModelConfig,
        schema: type[BaseModel],
        max_attempts: int = 2,
    ) -> Runnable[Any, BaseModel]:
        """按模型目录声明的策略创建带校验和有限重试的结构化模型。"""
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValidateErrorException("结构化输出尝试次数不能小于1")

        config = self.validate_model_config(model_config)
        provider = self.get_provider(config.provider)
        model_entity = provider.get_model_entity(config.model)
        provider_entity = provider.provider_entity
        strategy = (
            model_entity.structured_output_strategy
            or provider_entity.structured_output_strategy
        )
        strict = (
            model_entity.structured_output_strict
            if model_entity.structured_output_strict is not None
            else provider_entity.structured_output_strict
        )
        llm = self.create_language_model(config)

        if strategy == StructuredOutputStrategy.PROMPT:
            structured_model = llm | PydanticOutputParser(pydantic_object=schema)
        else:
            structured_kwargs: dict[str, Any] = {"method": strategy.value}
            if strategy == StructuredOutputStrategy.JSON_SCHEMA:
                structured_kwargs["strict"] = strict
            structured_model = llm.with_structured_output(schema, **structured_kwargs)

        def validate_structured_output(output: Any) -> BaseModel:
            if isinstance(output, schema):
                return output
            return schema.model_validate(output)

        validated_model = structured_model | RunnableLambda(validate_structured_output)
        return validated_model.with_retry(
            retry_if_exception_type=(ValidationError, OutputParserException),
            wait_exponential_jitter=False,
            stop_after_attempt=max_attempts,
        )

    def create_system_structured_chat_model(
        self,
        schema: type[BaseModel],
        parameters: Optional[Mapping[str, Any]] = None,
        max_attempts: int = 2,
    ) -> Runnable[Any, BaseModel]:
        """使用系统默认模型创建结构化输出模型。"""
        return self.create_structured_chat_model(
            self._get_system_model_config(parameters),
            schema,
            max_attempts=max_attempts,
        )

    @staticmethod
    def _get_system_model_config(
        parameters: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        return {
            "provider": os.getenv("SYSTEM_LLM_PROVIDER", "openai"),
            "model": os.getenv("SYSTEM_LLM_MODEL", "gpt-4o-mini"),
            "parameters": dict(parameters or {}),
        }

    @staticmethod
    def _validate_parameter_value(name: str, value: Any, rule: ModelParameter) -> None:
        """按照模型目录声明校验单个参数值。"""
        expected_type = rule.type
        valid_type = {
            ModelParameterType.FLOAT: lambda item: isinstance(item, (int, float))
            and not isinstance(item, bool),
            ModelParameterType.INT: lambda item: isinstance(item, int)
            and not isinstance(item, bool),
            ModelParameterType.STRING: lambda item: isinstance(item, str),
            ModelParameterType.BOOLEAN: lambda item: isinstance(item, bool),
        }[expected_type]
        if not valid_type(value):
            raise ValidateErrorException(f"模型参数{name}类型错误")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if isinstance(value, float) and not math.isfinite(value):
                raise ValidateErrorException(f"模型参数{name}必须是有限数值")
            if rule.min is not None and value < rule.min:
                raise ValidateErrorException(f"模型参数{name}不能小于{rule.min}")
            if rule.max is not None and value > rule.max:
                raise ValidateErrorException(f"模型参数{name}不能大于{rule.max}")
        if rule.options and value not in {option.value for option in rule.options}:
            raise ValidateErrorException(f"模型参数{name}不在允许范围内")


@lru_cache(maxsize=1)
def get_language_model_manager() -> LanguageModelManager:
    """为非 Injector 管理的 Core 对象提供共享的只读模型目录。"""
    return LanguageModelManager()
