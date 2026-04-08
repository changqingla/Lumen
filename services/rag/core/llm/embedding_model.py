#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

"""
DeepRAG 嵌入模型模块

本模块提供了多种嵌入模型的统一接口，支持：
- 云服务 API：OpenAI、通义千问、智谱AI、Gemini 等
- 自部署服务：VLLM、LocalAI等

所有模型类都继承自 Base 基类，提供统一的 encode 和 encode_queries 接口。
"""

# 标准库导入
import json
from abc import ABC
from urllib.parse import urljoin

# 第三方库导入
import dashscope                          # 阿里云通义千问 API
import google.generativeai as genai       # Google Gemini API
import numpy as np                        # 数值计算
import requests                           # HTTP 请求
from openai import OpenAI                 # OpenAI API 客户端
from zhipuai import ZhipuAI              # 智谱AI API 客户端

# 项目内部导入
from core.utils import log_exception  # 工具函数
from core.utils import num_tokens_from_string, truncate  # 文本处理工具


class Base(ABC):
    """
    嵌入模型基类

    所有嵌入模型都应该继承此类并实现相应的方法。
    提供统一的接口用于文本嵌入和查询嵌入。
    """

    def __init__(self, key, model_name):
        """
        初始化嵌入模型

        Args:
            key: API 密钥或认证信息
            model_name: 模型名称
        """
        pass

    def encode(self, texts: list):
        """
        批量编码文本为向量

        Args:
            texts: 待编码的文本列表

        Returns:
            tuple: (向量数组, token数量)

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError("Please implement encode method!")

    def encode_queries(self, text: str):
        """
        编码单个查询文本为向量

        Args:
            text: 待编码的查询文本

        Returns:
            tuple: (向量数组, token数量)

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError("Please implement encode method!")

    def total_token_count(self, resp):
        """
        从响应中提取总token数量

        Args:
            resp: API 响应对象

        Returns:
            int: token数量
        """
        try:
            return resp.usage.total_tokens
        except Exception:
            pass
        try:
            return resp["usage"]["total_tokens"]
        except Exception:
            pass
        return 0


class OpenAIEmbed(Base):
    """
    OpenAI 嵌入模型

    使用 OpenAI 的嵌入 API，支持 text-embedding-3-small、text-embedding-3-large 等模型。
    需要有效的 OpenAI API 密钥。

    特点：
    - 高质量的嵌入效果
    - 支持多语言
    - 按 token 计费
    - 批处理大小限制为 16
    """

    _FACTORY_NAME = "OpenAI"

    def __init__(self, key, model_name="text-embedding-ada-002", base_url="https://api.openai.com/v1"):
        """
        初始化 OpenAI 嵌入模型

        Args:
            key: OpenAI API 密钥
            model_name: 模型名称，默认为 "text-embedding-ada-002"
            base_url: API 基础 URL，默认为官方 API 地址
        """
        if not base_url:
            base_url = "https://api.openai.com/v1"
        self.client = OpenAI(api_key=key, base_url=base_url)
        self.model_name = model_name

    def encode(self, texts: list):
        """
        批量编码文本为向量

        Args:
            texts: 待编码的文本列表

        Returns:
            tuple: (向量数组, token总数)

        Note:
            OpenAI API 要求批处理大小不超过 16，文本长度不超过 8191 tokens
        """
        batch_size = 16  # OpenAI API 限制批处理大小
        texts = [truncate(t, 8191) for t in texts]  # 截断到最大长度
        ress = []
        total_tokens = 0

        # 分批处理
        for i in range(0, len(texts), batch_size):
            res = self.client.embeddings.create(input=texts[i : i + batch_size], model=self.model_name)
            try:
                ress.extend([d.embedding for d in res.data])
                total_tokens += self.total_token_count(res)
            except Exception as _e:
                log_exception(_e, res)
        return np.array(ress), total_tokens

    def encode_queries(self, text):
        """
        编码单个查询文本为向量

        Args:
            text: 待编码的查询文本

        Returns:
            tuple: (向量数组, token数量)
        """
        res = self.client.embeddings.create(input=[truncate(text, 8191)], model=self.model_name)
        return np.array(res.data[0].embedding), self.total_token_count(res)


class LocalAIEmbed(Base):
    """
    LocalAI 嵌入模型

    使用 LocalAI 框架部署的本地嵌入模型。LocalAI 提供与 OpenAI API 兼容的接口，
    可以在本地运行各种开源嵌入模型。

    特点：
    - 本地部署，数据隐私安全
    - 兼容 OpenAI API 格式
    - 支持多种开源模型
    - 无需 API 密钥费用
    """

    _FACTORY_NAME = "LocalAI"

    def __init__(self, key, model_name, base_url):
        """
        初始化 LocalAI 嵌入模型

        Args:
            key: API 密钥（LocalAI 通常不需要，但保留兼容性）
            model_name: 模型名称
            base_url: LocalAI 服务的基础 URL

        Raises:
            ValueError: 当 base_url 为空时抛出异常
        """
        if not base_url:
            raise ValueError("Local embedding model url cannot be None")
        base_url = urljoin(base_url, "v1")  # 确保 URL 以 /v1 结尾
        self.client = OpenAI(api_key="empty", base_url=base_url)  # 使用空密钥
        self.model_name = model_name.split("___")[0]  # 处理模型名称

    def encode(self, texts: list):
        """
        批量编码文本为向量

        Args:
            texts: 待编码的文本列表

        Returns:
            tuple: (向量数组, token总数)

        Note:
            LocalAI 通常不提供准确的 token 计数，因此使用估算值
        """
        batch_size = 16
        ress = []
        for i in range(0, len(texts), batch_size):
            res = self.client.embeddings.create(input=texts[i : i + batch_size], model=self.model_name)
            try:
                ress.extend([d.embedding for d in res.data])
            except Exception as _e:
                log_exception(_e, res)
        # LocalAI 和 LmStudio 通常不提供准确的 token 计数
        return np.array(ress), 1024

    def encode_queries(self, text):
        embds, cnt = self.encode([text])
        return np.array(embds[0]), cnt


class AzureEmbed(OpenAIEmbed):
    _FACTORY_NAME = "Azure-OpenAI"

    def __init__(self, key, model_name, **kwargs):
        from openai.lib.azure import AzureOpenAI

        api_key = json.loads(key).get("api_key", "")
        api_version = json.loads(key).get("api_version", "2024-02-01")
        self.client = AzureOpenAI(api_key=api_key, azure_endpoint=kwargs["base_url"], api_version=api_version)
        self.model_name = model_name


class BaiChuanEmbed(OpenAIEmbed):
    """
    百川智能嵌入模型

    使用百川智能的文本嵌入 API。百川智能是中国领先的大模型公司，
    提供高质量的中文文本嵌入服务。

    特点：
    - 中文效果优秀
    - 兼容 OpenAI API 格式
    - 支持多种文本类型
    """

    _FACTORY_NAME = "BaiChuan"

    def __init__(self, key, model_name="Baichuan-Text-Embedding", base_url="https://api.baichuan-ai.com/v1"):
        """
        初始化百川智能嵌入模型

        Args:
            key: 百川智能 API 密钥
            model_name: 模型名称，默认为 "Baichuan-Text-Embedding"
            base_url: API 基础 URL
        """
        if not base_url:
            base_url = "https://api.baichuan-ai.com/v1"
        super().__init__(key, model_name, base_url)


class QWenEmbed(Base):
    """
    通义千问嵌入模型

    使用阿里云通义千问的文本嵌入 API。通义千问是阿里巴巴开发的大语言模型，
    在中文理解和生成方面表现优秀。

    特点：
    - 中文效果优秀
    - 支持文档和查询两种文本类型
    - 自动重试机制
    - 批处理大小为 4
    """

    _FACTORY_NAME = "Tongyi-Qianwen"

    def __init__(self, key, model_name="text_embedding_v2", **kwargs):
        """
        初始化通义千问嵌入模型

        Args:
            key: 通义千问 API 密钥
            model_name: 模型名称，默认为 "text_embedding_v2"
            **kwargs: 其他参数
        """
        self.key = key
        self.model_name = model_name

    def encode(self, texts: list):
        """
        批量编码文档文本为向量

        Args:
            texts: 待编码的文本列表

        Returns:
            tuple: (向量数组, token总数)

        Note:
            使用 "document" 文本类型，适合长文档嵌入
            包含自动重试机制，最多重试 5 次
        """
        import dashscope
        import time

        batch_size = 4  # 通义千问推荐的批处理大小
        res = []
        token_count = 0
        texts = [truncate(t, 2048) for t in texts]  # 截断到最大长度

        for i in range(0, len(texts), batch_size):
            retry_max = 5  # 最大重试次数
            # 调用嵌入 API，指定文本类型为 "document"
            resp = dashscope.TextEmbedding.call(
                model=self.model_name,
                input=texts[i : i + batch_size],
                api_key=self.key,
                text_type="document"
            )

            # 重试机制：如果响应为空或没有嵌入结果，则重试
            while (resp["output"] is None or resp["output"].get("embeddings") is None) and retry_max > 0:
                time.sleep(10)  # 等待 10 秒后重试
                resp = dashscope.TextEmbedding.call(
                    model=self.model_name,
                    input=texts[i : i + batch_size],
                    api_key=self.key,
                    text_type="document"
                )
                retry_max -= 1

            # 如果重试次数用完仍然失败，抛出异常
            if retry_max == 0 and (resp["output"] is None or resp["output"].get("embeddings") is None):
                if resp.get("message"):
                    log_exception(ValueError(f"Retry_max reached, calling embedding model failed: {resp['message']}"))
                else:
                    log_exception(ValueError("Retry_max reached, calling embedding model failed"))
                raise

            try:
                # 按照文本索引重新排序嵌入结果
                embds = [[] for _ in range(len(resp["output"]["embeddings"]))]
                for e in resp["output"]["embeddings"]:
                    embds[e["text_index"]] = e["embedding"]
                res.extend(embds)
                token_count += self.total_token_count(resp)
            except Exception as _e:
                log_exception(_e, resp)
                raise
        return np.array(res), token_count

    def encode_queries(self, text):
        """
        编码单个查询文本为向量

        Args:
            text: 待编码的查询文本

        Returns:
            tuple: (向量数组, token数量)

        Note:
            使用 "query" 文本类型，适合查询文本嵌入
        """
        resp = dashscope.TextEmbedding.call(
            model=self.model_name,
            input=text[:2048],  # 截断到最大长度
            api_key=self.key,
            text_type="query"  # 查询类型
        )
        try:
            return np.array(resp["output"]["embeddings"][0]["embedding"]), self.total_token_count(resp)
        except Exception as _e:
            log_exception(_e, resp)


class ZhipuEmbed(Base):
    """
    智谱AI嵌入模型

    使用智谱AI的文本嵌入 API。智谱AI是清华大学技术成果转化的公司，
    提供高质量的中文大语言模型和嵌入服务。

    特点：
    - 中文效果优秀
    - 支持不同版本的嵌入模型
    - embedding-2: 最大长度 512 tokens
    - embedding-3: 最大长度 3072 tokens
    """

    _FACTORY_NAME = "ZHIPU-AI"

    def __init__(self, key, model_name="embedding-2", **kwargs):
        """
        初始化智谱AI嵌入模型

        Args:
            key: 智谱AI API 密钥
            model_name: 模型名称，默认为 "embedding-2"
            **kwargs: 其他参数
        """
        self.client = ZhipuAI(api_key=key)
        self.model_name = model_name

    def encode(self, texts: list):
        """
        批量编码文本为向量

        Args:
            texts: 待编码的文本列表

        Returns:
            tuple: (向量数组, token总数)

        Note:
            根据模型版本自动设置最大长度限制：
            - embedding-2: 512 tokens
            - embedding-3: 3072 tokens
        """
        arr = []
        tks_num = 0

        # 根据模型版本设置最大长度
        MAX_LEN = -1
        if self.model_name.lower() == "embedding-2":
            MAX_LEN = 512
        if self.model_name.lower() == "embedding-3":
            MAX_LEN = 3072
        if MAX_LEN > 0:
            texts = [truncate(t, MAX_LEN) for t in texts]

        # 逐个处理文本（智谱AI不支持批处理）
        for txt in texts:
            res = self.client.embeddings.create(input=txt, model=self.model_name)
            try:
                arr.append(res.data[0].embedding)
                tks_num += self.total_token_count(res)
            except Exception as _e:
                log_exception(_e, res)
        return np.array(arr), tks_num

    def encode_queries(self, text):
        """
        编码单个查询文本为向量

        Args:
            text: 待编码的查询文本

        Returns:
            tuple: (向量数组, token数量)
        """
        res = self.client.embeddings.create(input=text, model=self.model_name)
        try:
            return np.array(res.data[0].embedding), self.total_token_count(res)
        except Exception as _e:
            log_exception(_e, res)


class XinferenceEmbed(Base):
    _FACTORY_NAME = "Xinference"

    def __init__(self, key, model_name="", base_url=""):
        base_url = urljoin(base_url, "v1")
        self.client = OpenAI(api_key=key, base_url=base_url)
        self.model_name = model_name

    def encode(self, texts: list):
        batch_size = 16
        ress = []
        total_tokens = 0
        for i in range(0, len(texts), batch_size):
            res = self.client.embeddings.create(input=texts[i : i + batch_size], model=self.model_name)
            try:
                ress.extend([d.embedding for d in res.data])
                total_tokens += self.total_token_count(res)
            except Exception as _e:
                log_exception(_e, res)
        return np.array(ress), total_tokens

    def encode_queries(self, text):
        res = self.client.embeddings.create(input=[text], model=self.model_name)
        try:
            return np.array(res.data[0].embedding), self.total_token_count(res)
        except Exception as _e:
            log_exception(_e, res)


class JinaEmbed(Base):
    _FACTORY_NAME = "Jina"

    def __init__(self, key, model_name="jina-embeddings-v3", base_url="https://api.jina.ai/v1/embeddings"):
        self.base_url = "https://api.jina.ai/v1/embeddings"
        self.headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        self.model_name = model_name

    def encode(self, texts: list):
        texts = [truncate(t, 8196) for t in texts]
        batch_size = 16
        ress = []
        token_count = 0
        for i in range(0, len(texts), batch_size):
            data = {"model": self.model_name, "input": texts[i : i + batch_size], "encoding_type": "float"}
            response = requests.post(self.base_url, headers=self.headers, json=data)
            try:
                res = response.json()
                ress.extend([d["embedding"] for d in res["data"]])
                token_count += self.total_token_count(res)
            except Exception as _e:
                log_exception(_e, response)
        return np.array(ress), token_count

    def encode_queries(self, text):
        embds, cnt = self.encode([text])
        return np.array(embds[0]), cnt


class MistralEmbed(Base):
    _FACTORY_NAME = "Mistral"

    def __init__(self, key, model_name="mistral-embed", base_url=None):
        from mistralai.client import MistralClient

        self.client = MistralClient(api_key=key)
        self.model_name = model_name

    def encode(self, texts: list):
        texts = [truncate(t, 8196) for t in texts]
        batch_size = 16
        ress = []
        token_count = 0
        for i in range(0, len(texts), batch_size):
            res = self.client.embeddings(input=texts[i : i + batch_size], model=self.model_name)
            try:
                ress.extend([d.embedding for d in res.data])
                token_count += self.total_token_count(res)
            except Exception as _e:
                log_exception(_e, res)
        return np.array(ress), token_count

    def encode_queries(self, text):
        res = self.client.embeddings(input=[truncate(text, 8196)], model=self.model_name)
        try:
            return np.array(res.data[0].embedding), self.total_token_count(res)
        except Exception as _e:
            log_exception(_e, res)


class BedrockEmbed(Base):
    _FACTORY_NAME = "Bedrock"

    def __init__(self, key, model_name, **kwargs):
        import boto3

        self.bedrock_ak = json.loads(key).get("bedrock_ak", "")
        self.bedrock_sk = json.loads(key).get("bedrock_sk", "")
        self.bedrock_region = json.loads(key).get("bedrock_region", "")
        self.model_name = model_name

        if self.bedrock_ak == "" or self.bedrock_sk == "" or self.bedrock_region == "":
            # Try to create a client using the default credentials (AWS_PROFILE, AWS_DEFAULT_REGION, etc.)
            self.client = boto3.client("bedrock-runtime")
        else:
            self.client = boto3.client(service_name="bedrock-runtime", region_name=self.bedrock_region, aws_access_key_id=self.bedrock_ak, aws_secret_access_key=self.bedrock_sk)

    def encode(self, texts: list):
        texts = [truncate(t, 8196) for t in texts]
        embeddings = []
        token_count = 0
        for text in texts:
            if self.model_name.split(".")[0] == "amazon":
                body = {"inputText": text}
            elif self.model_name.split(".")[0] == "cohere":
                body = {"texts": [text], "input_type": "search_document"}

            response = self.client.invoke_model(modelId=self.model_name, body=json.dumps(body))
            try:
                model_response = json.loads(response["body"].read())
                embeddings.extend([model_response["embedding"]])
                token_count += num_tokens_from_string(text)
            except Exception as _e:
                log_exception(_e, response)

        return np.array(embeddings), token_count

    def encode_queries(self, text):
        embeddings = []
        token_count = num_tokens_from_string(text)
        if self.model_name.split(".")[0] == "amazon":
            body = {"inputText": truncate(text, 8196)}
        elif self.model_name.split(".")[0] == "cohere":
            body = {"texts": [truncate(text, 8196)], "input_type": "search_query"}

        response = self.client.invoke_model(modelId=self.model_name, body=json.dumps(body))
        try:
            model_response = json.loads(response["body"].read())
            embeddings.extend(model_response["embedding"])
        except Exception as _e:
            log_exception(_e, response)

        return np.array(embeddings), token_count


class GeminiEmbed(Base):
    _FACTORY_NAME = "Gemini"

    def __init__(self, key, model_name="models/text-embedding-004", **kwargs):
        self.key = key
        self.model_name = "models/" + model_name

    def encode(self, texts: list):
        texts = [truncate(t, 2048) for t in texts]
        token_count = sum(num_tokens_from_string(text) for text in texts)
        genai.configure(api_key=self.key)
        batch_size = 16
        ress = []
        for i in range(0, len(texts), batch_size):
            result = genai.embed_content(model=self.model_name, content=texts[i : i + batch_size], task_type="retrieval_document", title="Embedding of single string")
            try:
                ress.extend(result["embedding"])
            except Exception as _e:
                log_exception(_e, result)
        return np.array(ress), token_count

    def encode_queries(self, text):
        genai.configure(api_key=self.key)
        result = genai.embed_content(model=self.model_name, content=truncate(text, 2048), task_type="retrieval_document", title="Embedding of single string")
        token_count = num_tokens_from_string(text)
        try:
            return np.array(result["embedding"]), token_count
        except Exception as _e:
            log_exception(_e, result)


class NvidiaEmbed(Base):
    _FACTORY_NAME = "NVIDIA"

    def __init__(self, key, model_name, base_url="https://integrate.api.nvidia.com/v1/embeddings"):
        if not base_url:
            base_url = "https://integrate.api.nvidia.com/v1/embeddings"
        self.api_key = key
        self.base_url = base_url
        self.headers = {
            "accept": "application/json",
            "Content-Type": "application/json",
            "authorization": f"Bearer {self.api_key}",
        }
        self.model_name = model_name
        if model_name == "nvidia/embed-qa-4":
            self.base_url = "https://ai.api.nvidia.com/v1/retrieval/nvidia/embeddings"
            self.model_name = "NV-Embed-QA"
        if model_name == "snowflake/arctic-embed-l":
            self.base_url = "https://ai.api.nvidia.com/v1/retrieval/snowflake/arctic-embed-l/embeddings"

    def encode(self, texts: list):
        batch_size = 16
        ress = []
        token_count = 0
        for i in range(0, len(texts), batch_size):
            payload = {
                "input": texts[i : i + batch_size],
                "input_type": "query",
                "model": self.model_name,
                "encoding_format": "float",
                "truncate": "END",
            }
            response = requests.post(self.base_url, headers=self.headers, json=payload)
            try:
                res = response.json()
            except Exception as _e:
                log_exception(_e, response)
            ress.extend([d["embedding"] for d in res["data"]])
            token_count += self.total_token_count(res)
        return np.array(ress), token_count

    def encode_queries(self, text):
        embds, cnt = self.encode([text])
        return np.array(embds[0]), cnt


class LmStudioEmbed(LocalAIEmbed):
    _FACTORY_NAME = "LM-Studio"

    def __init__(self, key, model_name, base_url):
        if not base_url:
            raise ValueError("Local llm url cannot be None")
        base_url = urljoin(base_url, "v1")
        self.client = OpenAI(api_key="lm-studio", base_url=base_url)
        self.model_name = model_name


class OpenAI_APIEmbed(OpenAIEmbed):
    """
    OpenAI API 兼容的嵌入模型

    支持所有兼容 OpenAI API 格式的嵌入服务，包括：
    - VLLM: 高性能推理引擎
    - 其他 OpenAI API 兼容服务

    继承自 OpenAIEmbed，复用其编码逻辑，只需要不同的初始化参数。

    特点：
    - 兼容 OpenAI API 格式
    - 支持自定义服务端点
    - 高性能推理
    - 灵活的模型选择
    """

    _FACTORY_NAME = ["VLLM", "OpenAI-API-Compatible", "openai","OpenAI"]  # 支持多个工厂名称

    def __init__(self, key, model_name, base_url):
        """
        初始化 OpenAI API 兼容的嵌入模型

        Args:
            key: API 密钥（某些服务可能不需要）
            model_name: 模型名称
            base_url: 服务的基础 URL

        Raises:
            ValueError: 当 base_url 为空时抛出异常
        """
        if not base_url:
            raise ValueError("url cannot be None")
        base_url = urljoin(base_url, "v1")  # 确保 URL 以 /v1 结尾
        self.client = OpenAI(api_key=key, base_url=base_url)
        self.model_name = model_name.split("___")[0]  # 处理模型名称


class CoHereEmbed(Base):
    _FACTORY_NAME = "Cohere"

    def __init__(self, key, model_name, base_url=None):
        from cohere import Client

        self.client = Client(api_key=key)
        self.model_name = model_name

    def encode(self, texts: list):
        batch_size = 16
        ress = []
        token_count = 0
        for i in range(0, len(texts), batch_size):
            res = self.client.embed(
                texts=texts[i : i + batch_size],
                model=self.model_name,
                input_type="search_document",
                embedding_types=["float"],
            )
            try:
                ress.extend([d for d in res.embeddings.float])
                token_count += res.meta.billed_units.input_tokens
            except Exception as _e:
                log_exception(_e, res)
        return np.array(ress), token_count

    def encode_queries(self, text):
        res = self.client.embed(
            texts=[text],
            model=self.model_name,
            input_type="search_query",
            embedding_types=["float"],
        )
        try:
            return np.array(res.embeddings.float[0]), int(res.meta.billed_units.input_tokens)
        except Exception as _e:
            log_exception(_e, res)


class TogetherAIEmbed(OpenAIEmbed):
    _FACTORY_NAME = "TogetherAI"

    def __init__(self, key, model_name, base_url="https://api.together.xyz/v1"):
        if not base_url:
            base_url = "https://api.together.xyz/v1"
        super().__init__(key, model_name, base_url=base_url)


class PerfXCloudEmbed(OpenAIEmbed):
    _FACTORY_NAME = "PerfXCloud"

    def __init__(self, key, model_name, base_url="https://cloud.perfxlab.cn/v1"):
        if not base_url:
            base_url = "https://cloud.perfxlab.cn/v1"
        super().__init__(key, model_name, base_url)


class UpstageEmbed(OpenAIEmbed):
    _FACTORY_NAME = "Upstage"

    def __init__(self, key, model_name, base_url="https://api.upstage.ai/v1/solar"):
        if not base_url:
            base_url = "https://api.upstage.ai/v1/solar"
        super().__init__(key, model_name, base_url)


class SILICONFLOWEmbed(Base):
    _FACTORY_NAME = "SILICONFLOW"

    def __init__(self, key, model_name, **kwargs):
        """
        初始化 SILICONFLOW 嵌入模型
        
        Args:
            key: API 密钥
            model_name: 模型名称
            
        Note:
            base_url 始终使用默认值，不接受外部传入
        """
        self.base_url = "https://api.siliconflow.cn/v1/embeddings"
        self.headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Bearer {key}",
        }
        self.model_name = model_name

    def encode(self, texts: list):
        batch_size = 16
        ress = []
        token_count = 0
        for i in range(0, len(texts), batch_size):
            texts_batch = texts[i : i + batch_size]
            payload = {
                "model": self.model_name,
                "input": texts_batch,
                "encoding_format": "float",
            }
            response = requests.post(self.base_url, json=payload, headers=self.headers)
            try:
                res = response.json()
                ress.extend([d["embedding"] for d in res["data"]])
                token_count += self.total_token_count(res)
            except Exception as _e:
                log_exception(_e, response)

        return np.array(ress), token_count

    def encode_queries(self, text):
        payload = {
            "model": self.model_name,
            "input": text,
            "encoding_format": "float",
        }
        response = requests.post(self.base_url, json=payload, headers=self.headers)
        try:
            res = response.json()
            return np.array(res["data"][0]["embedding"]), self.total_token_count(res)
        except Exception as _e:
            log_exception(_e, response)


class ReplicateEmbed(Base):
    _FACTORY_NAME = "Replicate"

    def __init__(self, key, model_name, base_url=None):
        from replicate.client import Client

        self.model_name = model_name
        self.client = Client(api_token=key)

    def encode(self, texts: list):
        batch_size = 16
        token_count = sum([num_tokens_from_string(text) for text in texts])
        ress = []
        for i in range(0, len(texts), batch_size):
            res = self.client.run(self.model_name, input={"texts": texts[i : i + batch_size]})
            ress.extend(res)
        return np.array(ress), token_count

    def encode_queries(self, text):
        res = self.client.embed(self.model_name, input={"texts": [text]})
        return np.array(res), num_tokens_from_string(text)


class BaiduYiyanEmbed(Base):
    _FACTORY_NAME = "BaiduYiyan"

    def __init__(self, key, model_name, base_url=None):
        import qianfan

        key = json.loads(key)
        ak = key.get("yiyan_ak", "")
        sk = key.get("yiyan_sk", "")
        self.client = qianfan.Embedding(ak=ak, sk=sk)
        self.model_name = model_name

    def encode(self, texts: list, batch_size=16):
        res = self.client.do(model=self.model_name, texts=texts).body
        try:
            return (
                np.array([r["embedding"] for r in res["data"]]),
                self.total_token_count(res),
            )
        except Exception as _e:
            log_exception(_e, res)

    def encode_queries(self, text):
        res = self.client.do(model=self.model_name, texts=[text]).body
        try:
            return (
                np.array([r["embedding"] for r in res["data"]]),
                self.total_token_count(res),
            )
        except Exception as _e:
            log_exception(_e, res)


class VoyageEmbed(Base):
    _FACTORY_NAME = "Voyage AI"

    def __init__(self, key, model_name, base_url=None):
        import voyageai

        self.client = voyageai.Client(api_key=key)
        self.model_name = model_name

    def encode(self, texts: list):
        batch_size = 16
        ress = []
        token_count = 0
        for i in range(0, len(texts), batch_size):
            res = self.client.embed(texts=texts[i : i + batch_size], model=self.model_name, input_type="document")
            try:
                ress.extend(res.embeddings)
                token_count += res.total_tokens
            except Exception as _e:
                log_exception(_e, res)
        return np.array(ress), token_count

    def encode_queries(self, text):
        res = self.client.embed(texts=text, model=self.model_name, input_type="query")
        try:
            return np.array(res.embeddings)[0], res.total_tokens
        except Exception as _e:
            log_exception(_e, res)


class HuggingFaceEmbed(Base):
    _FACTORY_NAME = "HuggingFace"

    def __init__(self, key, model_name, base_url=None):
        if not model_name:
            raise ValueError("Model name cannot be None")
        self.key = key
        self.model_name = model_name.split("___")[0]
        self.base_url = base_url or "http://127.0.0.1:8080"

    def encode(self, texts: list):
        embeddings = []
        for text in texts:
            response = requests.post(f"{self.base_url}/embed", json={"inputs": text}, headers={"Content-Type": "application/json"})
            if response.status_code == 200:
                embedding = response.json()
                embeddings.append(embedding[0])
            else:
                raise Exception(f"Error: {response.status_code} - {response.text}")
        return np.array(embeddings), sum([num_tokens_from_string(text) for text in texts])

    def encode_queries(self, text):
        response = requests.post(f"{self.base_url}/embed", json={"inputs": text}, headers={"Content-Type": "application/json"})
        if response.status_code == 200:
            embedding = response.json()
            return np.array(embedding[0]), num_tokens_from_string(text)
        else:
            raise Exception(f"Error: {response.status_code} - {response.text}")


class VolcEngineEmbed(OpenAIEmbed):
    _FACTORY_NAME = "VolcEngine"

    def __init__(self, key, model_name, base_url="https://ark.cn-beijing.volces.com/api/v3"):
        if not base_url:
            base_url = "https://ark.cn-beijing.volces.com/api/v3"
        ark_api_key = json.loads(key).get("ark_api_key", "")
        model_name = json.loads(key).get("ep_id", "") + json.loads(key).get("endpoint_id", "")
        super().__init__(ark_api_key, model_name, base_url)


class GPUStackEmbed(OpenAIEmbed):
    _FACTORY_NAME = "GPUStack"

    def __init__(self, key, model_name, base_url):
        if not base_url:
            raise ValueError("url cannot be None")
        base_url = urljoin(base_url, "v1")

        self.client = OpenAI(api_key=key, base_url=base_url)
        self.model_name = model_name


class NovitaEmbed(Base):
    _FACTORY_NAME = "NovitaAI"

    def __init__(self, key, model_name):
        """
        初始化 NovitaAI 嵌入模型
        
        Args:
            key: API 密钥
            model_name: 模型名称
            
        Note:
            base_url 始终使用默认值，不接受外部传入
        """
        self.base_url = "https://api.novita.ai/v3/openai/embeddings"
        self.headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Bearer {key}",
        }
        self.model_name = model_name

    def encode(self, texts: list):
        batch_size = 16
        ress = []
        token_count = 0
        for i in range(0, len(texts), batch_size):
            texts_batch = texts[i : i + batch_size]
            payload = {
                "model": self.model_name,
                "input": texts_batch,
                "encoding_format": "float",
            }
            response = requests.post(self.base_url, json=payload, headers=self.headers)
            try:
                res = response.json()
                ress.extend([d["embedding"] for d in res["data"]])
                token_count += self.total_token_count(res)
            except Exception as _e:
                log_exception(_e, response)

        return np.array(ress), token_count

    def encode_queries(self, text):
        payload = {
            "model": self.model_name,
            "input": text,
            "encoding_format": "float",
        }
        response = requests.post(self.base_url, json=payload, headers=self.headers)
        try:
            res = response.json()
            return np.array(res["data"][0]["embedding"]), self.total_token_count(res)
        except Exception as _e:
            log_exception(_e, response)


class GiteeEmbed(Base):
    _FACTORY_NAME = "GiteeAI"

    def __init__(self, key, model_name):
        """
        初始化 GiteeAI 嵌入模型
        
        Args:
            key: API 密钥
            model_name: 模型名称
            
        Note:
            base_url 始终使用默认值，不接受外部传入
        """
        self.base_url = "https://ai.gitee.com/v1/embeddings"
        self.headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Bearer {key}",
        }
        self.model_name = model_name

    def encode(self, texts: list):
        batch_size = 16
        ress = []
        token_count = 0
        for i in range(0, len(texts), batch_size):
            texts_batch = texts[i : i + batch_size]
            payload = {
                "model": self.model_name,
                "input": texts_batch,
                "encoding_format": "float",
            }
            response = requests.post(self.base_url, json=payload, headers=self.headers)
            try:
                res = response.json()
                ress.extend([d["embedding"] for d in res["data"]])
                token_count += self.total_token_count(res)
            except Exception as _e:
                log_exception(_e, response)

        return np.array(ress), token_count

    def encode_queries(self, text):
        payload = {
            "model": self.model_name,
            "input": text,
            "encoding_format": "float",
        }
        response = requests.post(self.base_url, json=payload, headers=self.headers)
        try:
            res = response.json()
            return np.array(res["data"][0]["embedding"]), self.total_token_count(res)
        except Exception as _e:
            log_exception(_e, response)