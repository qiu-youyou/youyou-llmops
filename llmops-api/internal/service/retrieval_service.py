#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File   :   retrieval_service
@Time   :   2026/1/16
@Author :   s.qiu@foxmail.com
"""

from dataclasses import dataclass
from uuid import UUID

from flask import Flask
from injector import inject
from langchain_classic.retrievers import EnsembleRetriever
from langchain_core.documents import Document as LCDocument
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import update

from internal.core.agent.entities.agent_entity import DATASET_RETRIEVAL_TOOL_NAME
from internal.entity.dataset_entity import (
    DocumentStatus,
    RetrievalStrategy,
    RetrievalSource,
    SegmentStatus,
)
from internal.exception import NotFoundException
from internal.lib.helper import combine_documents
from internal.model import Dataset, DatasetQuery, Document, Segment
from internal.service.base_service import BaseService
from internal.service.jieba_service import JiebaService
from internal.service.vector_database_service import VectorDatabaseService
from pkg.sqlalchemy import SQLAlchemy


@inject
@dataclass
class RetrievalService(BaseService):
    """检索服务"""

    db: SQLAlchemy
    jieba_service: JiebaService
    vector_database_service: VectorDatabaseService

    def search_in_datasets(
        self,
        dataset_ids: list[UUID],
        query: str,
        account_id: UUID,
        retrieval_strategy: str = RetrievalStrategy.SEMANTIC,
        k: int = 4,
        score: float = 0,
        retrival_source: str = RetrievalSource.HIT_TESTING,
    ) -> list[LCDocument]:
        """知识库检索 返回检索文档+得分 全文检索则得分为0"""

        # 提取知识库列表校验权限
        datasets = (
            self.db.session.query(Dataset)
            .filter(Dataset.id.in_(dataset_ids), Dataset.account_id == account_id)
            .all()
        )
        if datasets is None or len(datasets) == 0:
            raise NotFoundException("当前无知识库可进行检索")
        dataset_ids = [dataset.id for dataset in datasets]

        # 构建不同种类检索器
        from internal.core.retrievers import SemanticRetriever, FullTextRetriever

        # 相似性/向量 检索器
        semantic_retriever = SemanticRetriever(
            dataset_ids=dataset_ids,
            vector_store=self.vector_database_service.vector_store,
            search_kwargs={"k": k, "score_threshold": score},
        )
        # 全文检索器
        full_text_retriever = FullTextRetriever(
            db=self.db,
            dataset_ids=dataset_ids,
            jieba_services=self.jieba_service,
            search_kwargs={"k": k},
        )
        # 混合检索器
        hybrid_retriever = EnsembleRetriever(
            retrievers=[semantic_retriever, full_text_retriever], weights=[0.5, 0.5]
        )

        # 执行不同检索策略
        if retrieval_strategy == RetrievalStrategy.SEMANTIC:
            lc_documents = semantic_retriever.invoke(query)[:k]
        elif retrieval_strategy == RetrievalStrategy.FULL_TEXT:
            lc_documents = full_text_retriever.invoke(query)[:k]
        else:
            lc_documents = hybrid_retriever.invoke(query)[:k]

        # 向量库删除或启停同步可能滞后。最终以 PostgreSQL 中仍可见的片段为准，
        # 也避免已经删除的向量参与查询记录与命中计数。
        visible_segments = self._get_visible_segments(
            [item.metadata.get("segment_id") for item in lc_documents],
            dataset_ids,
            account_id,
        )
        lc_documents = [
            item
            for item in lc_documents
            if str(item.metadata.get("segment_id")) in visible_segments
            # if item.metadata.get("segment_id") in visible_segments
        ]

        # 知识库查询记录 存储唯一记录
        unique_dataset_ids = list(
            set(str(lc_document.metadata["dataset_id"]) for lc_document in lc_documents)
        )
        for dataset_id in unique_dataset_ids:
            self.create(
                DatasetQuery,
                dataset_id=dataset_id,
                query=query,
                source=retrival_source,
                # todo:9 等待APP配置模块完成后进行调整
                source_app_id=None,
                created_by=account_id,
            )

        # 更新片段的命中次数 召回次数
        self._increment_hits(list(visible_segments.values()))

        return lc_documents

    def _get_visible_segments(
        self,
        segment_ids: list[str | UUID | None],
        dataset_ids: list[UUID],
        account_id: UUID,
    ) -> dict[str, UUID]:
        """以主库状态过滤来自关键词表或向量库的候选片段。"""
        segment_ids = [segment_id for segment_id in segment_ids if segment_id]
        if not segment_ids:
            return {}
        records = (
            self.db.session.query(Segment.id)
            .join(Document, Document.id == Segment.document_id)
            .filter(
                Segment.id.in_(segment_ids),
                Segment.dataset_id.in_(dataset_ids),
                Segment.account_id == account_id,
                Segment.status == SegmentStatus.COMPLETED,
                Segment.enabled.is_(True),
                Document.status == DocumentStatus.COMPLETED,
                Document.enabled.is_(True),
            )
            .all()
        )
        return {str(record.id): record.id for record in records}

    def _increment_hits(self, segment_ids: list[UUID]) -> None:
        if not segment_ids:
            return
        with self.db.auto_commit():
            self.db.session.execute(
                update(Segment)
                .where(Segment.id.in_(segment_ids))
                .values(hit_count=Segment.hit_count + 1)
            )

    def create_langchain_tool_from_search(
        self,
        flask_app: Flask,
        dataset_ids: list[UUID],
        account_id: UUID,
        retrieval_strategy: str = RetrievalStrategy.SEMANTIC,
        k: int = 4,
        score: float = 0,
        retrival_source: str = RetrievalSource.HIT_TESTING,
    ):
        """构建一个 LangChain知识库检索工具"""

        class DatasetRetrievalInput(BaseModel):
            """知识库检索工具输入结构"""

            query: str = Field(description="知识库搜索query语句，类型为字符串")

        @tool(DATASET_RETRIEVAL_TOOL_NAME, args_schema=DatasetRetrievalInput)
        def dataset_retrieval(query: str) -> str:
            """如果需要搜索扩展的知识库内容，当你觉得用户的提问超过你的知识范围时，可以尝试调用该工具，输入为搜索query语句，返回数据为检索内容字符串"""
            # 调用search_in_datasets检索得到LangChain文档列表
            with flask_app.app_context():
                documents = self.search_in_datasets(
                    dataset_ids=dataset_ids,
                    query=query,
                    account_id=account_id,
                    retrieval_strategy=retrieval_strategy,
                    k=k,
                    score=score,
                    retrival_source=retrival_source,
                )

            # 将LangChain文档列表转换成字符串后返回
            if len(documents) == 0:
                return "知识库内没有检索到对应内容"

            return combine_documents(documents)

        return dataset_retrieval
