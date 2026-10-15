import pytest
from types import SimpleNamespace
from unittest.mock import Mock

# 与应用入口保持相同的服务包导入顺序，避免现有聚合导入的循环依赖。
import internal.service  # noqa: F401
from internal.core.file_extractor.file_extractor import FileExtractor
from internal.core.file_extractor import file_extractor as module


@pytest.mark.parametrize("return_text", [False, True])
@pytest.mark.parametrize("suffix", [".txt", ".TXT"])
def test_json_shaped_text_preserves_chinese_and_literal_escapes(tmp_path, return_text, suffix):
    source = '{"商品简介": "晴空手机", "示例": "\\\\u5546", "价格": 599}\n'
    path = tmp_path / f"商品{suffix}"
    path.write_text(source, encoding="utf-8")

    result = FileExtractor.load_from_file(str(path), return_text=return_text)

    content = result if return_text else result[0].page_content
    assert content == source


def test_plain_text_preserves_chinese_and_paragraphs(tmp_path):
    source = "商品介绍\n\n支持双卡双待，价格 599 元。\n"
    path = tmp_path / "商品.txt"
    path.write_text(source, encoding="utf-8")

    assert FileExtractor.load_from_file(str(path), return_text=True) == source


def test_url_query_does_not_change_text_loader(monkeypatch):
    source = '{"商品简介": "中文原文"}'
    request = Mock(return_value=SimpleNamespace(content=source.encode("utf-8")))
    monkeypatch.setattr(module.requests, "get", request)
    url = "https://example.invalid/%E5%95%86%E5%93%81.txt?download=1#preview"

    assert FileExtractor.load_from_url(url, return_text=True) == source
    request.assert_called_once_with(url)


@pytest.mark.parametrize("suffix,loader_name", [
    (".doc", "UnstructuredWordDocumentLoader"),
    (".docx", "UnstructuredWordDocumentLoader"),
    (".pptx", "UnstructuredPowerPointLoader"),
    (".md", "UnstructuredMarkdownLoader"),
    (".pdf", "UnstructuredPDFLoader"),
    (".html", "UnstructuredHTMLLoader"),
    (".csv", "UnstructuredCSVLoader"),
    (".xlsx", "UnstructuredExcelLoader"),
])
def test_known_formats_use_explicit_loader_once(monkeypatch, tmp_path, suffix, loader_name):
    loader = Mock()
    loader.return_value.load.return_value = [module.LCDocument(page_content="中文原文")]
    monkeypatch.setattr(module, loader_name, loader)
    fallback = Mock(side_effect=AssertionError("已知格式不应自动识别"))
    monkeypatch.setattr(module, "UnstructuredFileLoader", fallback)
    path = str(tmp_path / f"文档{suffix}")

    assert FileExtractor.load_from_file(path, return_text=True) == "中文原文"
    loader.assert_called_once_with(path)
    loader.return_value.load.assert_called_once_with()
