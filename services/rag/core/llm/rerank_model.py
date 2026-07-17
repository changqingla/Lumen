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
import json
from abc import ABC
from urllib.parse import urljoin

import numpy as np
from yarl import URL

from core.provider_http import ProviderHTTPError, provider_post
from core.utils import log_exception
from core.utils import num_tokens_from_string, truncate

class Base(ABC):
    def __init__(self, key, model_name):
        pass

    def similarity(self, query: str, texts: list):
        raise NotImplementedError("Please implement encode method!")

    def total_token_count(self, resp):
        try:
            return resp.usage.total_tokens
        except Exception:
            pass
        try:
            return resp["usage"]["total_tokens"]
        except Exception:
            pass
        return 0


class JinaRerank(Base):
    _FACTORY_NAME = "Jina"

    def __init__(self, key, model_name="jina-reranker-v2-base-multilingual", base_url="https://api.jina.ai/v1/rerank"):
        self.base_url = "https://api.jina.ai/v1/rerank"
        self.headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        self.model_name = model_name

    def similarity(self, query: str, texts: list):
        texts = [truncate(t, 8196) for t in texts]
        data = {"model": self.model_name, "query": query, "documents": texts, "top_n": len(texts)}
        res = provider_post(self.base_url, headers=self.headers, json=data).json()
        rank = np.zeros(len(texts), dtype=float)
        try:
            for d in res["results"]:
                rank[d["index"]] = d["relevance_score"]
        except Exception as _e:
            log_exception(_e, res)
        return rank, self.total_token_count(res)


class XInferenceRerank(Base):
    _FACTORY_NAME = "Xinference"

    def __init__(self, key="x", model_name="", base_url=""):
        if base_url.find("/v1") == -1:
            base_url = urljoin(base_url, "/v1/rerank")
        if base_url.find("/rerank") == -1:
            base_url = urljoin(base_url, "/v1/rerank")
        self.model_name = model_name
        self.base_url = base_url
        self.headers = {"Content-Type": "application/json", "accept": "application/json"}
        if key and key != "x":
            self.headers["Authorization"] = f"Bearer {key}"

    def similarity(self, query: str, texts: list):
        if len(texts) == 0:
            return np.array([]), 0
        pairs = [(query, truncate(t, 4096)) for t in texts]
        token_count = 0
        for _, t in pairs:
            token_count += num_tokens_from_string(t)
        data = {"model": self.model_name, "query": query, "return_documents": "true", "return_len": "true", "documents": texts}
        res = provider_post(self.base_url, headers=self.headers, json=data).json()
        rank = np.zeros(len(texts), dtype=float)
        try:
            for d in res["results"]:
                rank[d["index"]] = d["relevance_score"]
        except Exception as _e:
            log_exception(_e, res)
        return rank, token_count


class LocalAIRerank(Base):
    _FACTORY_NAME = "LocalAI"

    def __init__(self, key, model_name, base_url):
        if base_url.find("/rerank") == -1:
            self.base_url = urljoin(base_url, "/rerank")
        else:
            self.base_url = base_url
        self.headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        self.model_name = model_name.split("___")[0]

    def similarity(self, query: str, texts: list):
        # noway to config Ragflow , use fix setting
        texts = [truncate(t, 500) for t in texts]
        data = {
            "model": self.model_name,
            "query": query,
            "documents": texts,
            "top_n": len(texts),
        }
        token_count = 0
        for t in texts:
            token_count += num_tokens_from_string(t)
        res = provider_post(self.base_url, headers=self.headers, json=data).json()
        rank = np.zeros(len(texts), dtype=float)
        try:
            for d in res["results"]:
                rank[d["index"]] = d["relevance_score"]
        except Exception as _e:
            log_exception(_e, res)

        # Normalize the rank values to the range 0 to 1
        min_rank = np.min(rank)
        max_rank = np.max(rank)

        # Avoid division by zero if all ranks are identical
        if max_rank - min_rank != 0:
            rank = (rank - min_rank) / (max_rank - min_rank)
        else:
            rank = np.zeros_like(rank)

        return rank, token_count


class NvidiaRerank(Base):
    _FACTORY_NAME = "NVIDIA"

    def __init__(self, key, model_name, base_url="https://ai.api.nvidia.com/v1/retrieval/nvidia/"):
        if not base_url:
            base_url = "https://ai.api.nvidia.com/v1/retrieval/nvidia/"
        self.model_name = model_name

        if self.model_name == "nvidia/nv-rerankqa-mistral-4b-v3":
            self.base_url = urljoin(base_url, "nv-rerankqa-mistral-4b-v3/reranking")

        if self.model_name == "nvidia/rerank-qa-mistral-4b":
            self.base_url = urljoin(base_url, "reranking")
            self.model_name = "nv-rerank-qa-mistral-4b:1"

        self.headers = {
            "accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        }

    def similarity(self, query: str, texts: list):
        token_count = num_tokens_from_string(query) + sum([num_tokens_from_string(t) for t in texts])
        data = {
            "model": self.model_name,
            "query": {"text": query},
            "passages": [{"text": text} for text in texts],
            "truncate": "END",
            "top_n": len(texts),
        }
        res = provider_post(self.base_url, headers=self.headers, json=data).json()
        rank = np.zeros(len(texts), dtype=float)
        try:
            for d in res["rankings"]:
                rank[d["index"]] = d["logit"]
        except Exception as _e:
            log_exception(_e, res)
        return rank, token_count


class LmStudioRerank(Base):
    _FACTORY_NAME = "LM-Studio"

    def __init__(self, key, model_name, base_url):
        pass

    def similarity(self, query: str, texts: list):
        raise NotImplementedError("The LmStudioRerank has not been implement")


class OpenAI_APIRerank(Base):
    _FACTORY_NAME = ["OpenAI-API-Compatible", "openai","OpenAI"]

    def __init__(self, key, model_name, base_url):
        if base_url.find("/rerank") == -1:
            self.base_url = urljoin(base_url, "/rerank")
        else:
            self.base_url = base_url
        self.headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        self.model_name = model_name.split("___")[0]

    def similarity(self, query: str, texts: list):
        # noway to config Ragflow , use fix setting
        texts = [truncate(t, 500) for t in texts]
        data = {
            "model": self.model_name,
            "query": query,
            "documents": texts,
            "top_n": len(texts),
        }
        token_count = 0
        for t in texts:
            token_count += num_tokens_from_string(t)
        res = provider_post(self.base_url, headers=self.headers, json=data).json()
        rank = np.zeros(len(texts), dtype=float)
        try:
            for d in res["results"]:
                rank[d["index"]] = d["relevance_score"]
        except Exception as _e:
            log_exception(_e, res)

        # Normalize the rank values to the range 0 to 1
        min_rank = np.min(rank)
        max_rank = np.max(rank)

        # Avoid division by zero if all ranks are identical
        if max_rank - min_rank != 0:
            rank = (rank - min_rank) / (max_rank - min_rank)
        else:
            rank = np.zeros_like(rank)

        return rank, token_count


class CoHereRerank(Base):
    _FACTORY_NAME = "Cohere"

    def __init__(self, key, model_name, base_url=None):
        from cohere import Client

        self.client = Client(api_key=key, base_url=base_url)
        self.model_name = model_name.split("___")[0]

    def similarity(self, query: str, texts: list):
        token_count = num_tokens_from_string(query) + sum([num_tokens_from_string(t) for t in texts])
        res = self.client.rerank(
            model=self.model_name,
            query=query,
            documents=texts,
            top_n=len(texts),
            return_documents=False,
        )
        rank = np.zeros(len(texts), dtype=float)
        try:
            for d in res.results:
                rank[d.index] = d.relevance_score
        except Exception as _e:
            log_exception(_e, res)
        return rank, token_count


class TogetherAIRerank(Base):
    _FACTORY_NAME = "TogetherAI"

    def __init__(self, key, model_name, base_url):
        pass

    def similarity(self, query: str, texts: list):
        raise NotImplementedError("The api has not been implement")


class SILICONFLOWRerank(Base):
    _FACTORY_NAME = "SILICONFLOW"

    def __init__(self, key, model_name, base_url="https://api.siliconflow.cn/v1/rerank"):
        # if not base_url:
        #     base_url = "https://api.siliconflow.cn/v1/rerank"
        base_url = "https://api.siliconflow.cn/v1/rerank"
        self.model_name = model_name
        self.base_url = base_url
        self.headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Bearer {key}",
        }

    def similarity(self, query: str, texts: list):
        payload = {
            "model": self.model_name,
            "query": query,
            "documents": texts,
            "top_n": len(texts),
            "return_documents": False,
            "max_chunks_per_doc": 1024,
            "overlap_tokens": 80,
        }
        response = provider_post(self.base_url, json=payload, headers=self.headers).json()
        rank = np.zeros(len(texts), dtype=float)
        try:
            for d in response["results"]:
                rank[d["index"]] = d["relevance_score"]
        except Exception as _e:
            log_exception(_e, response)
        return (
            rank,
            response["meta"]["tokens"]["input_tokens"] + response["meta"]["tokens"]["output_tokens"],
        )


class BaiduYiyanRerank(Base):
    _FACTORY_NAME = "BaiduYiyan"

    def __init__(self, key, model_name, base_url=None):
        from qianfan.resources import Reranker

        key = json.loads(key)
        ak = key.get("yiyan_ak", "")
        sk = key.get("yiyan_sk", "")
        self.client = Reranker(ak=ak, sk=sk)
        self.model_name = model_name

    def similarity(self, query: str, texts: list):
        res = self.client.do(
            model=self.model_name,
            query=query,
            documents=texts,
            top_n=len(texts),
        ).body
        rank = np.zeros(len(texts), dtype=float)
        try:
            for d in res["results"]:
                rank[d["index"]] = d["relevance_score"]
        except Exception as _e:
            log_exception(_e, res)
        return rank, self.total_token_count(res)


class VoyageRerank(Base):
    _FACTORY_NAME = "Voyage AI"

    def __init__(self, key, model_name, base_url=None):
        import voyageai

        self.client = voyageai.Client(api_key=key)
        self.model_name = model_name

    def similarity(self, query: str, texts: list):
        rank = np.zeros(len(texts), dtype=float)
        if not texts:
            return rank, 0
        res = self.client.rerank(query=query, documents=texts, model=self.model_name, top_k=len(texts))
        try:
            for r in res.results:
                rank[r.index] = r.relevance_score
        except Exception as _e:
            log_exception(_e, res)
        return rank, res.total_tokens


class QWenRerank(Base):
    _FACTORY_NAME = "Tongyi-Qianwen"

    def __init__(self, key, model_name="gte-rerank", base_url=None, **kwargs):
        import dashscope

        self.api_key = key
        self.model_name = dashscope.TextReRank.Models.gte_rerank if model_name is None else model_name

    def similarity(self, query: str, texts: list):
        from http import HTTPStatus

        import dashscope

        resp = dashscope.TextReRank.call(api_key=self.api_key, model=self.model_name, query=query, documents=texts, top_n=len(texts), return_documents=False)
        rank = np.zeros(len(texts), dtype=float)
        if resp.status_code == HTTPStatus.OK:
            try:
                for r in resp.output.results:
                    rank[r.index] = r.relevance_score
            except Exception as _e:
                log_exception(_e, resp)
            return rank, resp.usage.total_tokens
        else:
            status = resp.status_code if isinstance(resp.status_code, int) else "unknown"
            raise ValueError(
                f"QWen rerank request failed: status={status}"
            ) from None


class GPUStackRerank(Base):
    _FACTORY_NAME = "GPUStack"

    def __init__(self, key, model_name, base_url):
        if not base_url:
            raise ValueError("url cannot be None")

        self.model_name = model_name
        self.base_url = str(URL(base_url) / "v1" / "rerank")
        self.headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Bearer {key}",
        }

    def similarity(self, query: str, texts: list):
        payload = {
            "model": self.model_name,
            "query": query,
            "documents": texts,
            "top_n": len(texts),
        }

        try:
            response = provider_post(self.base_url, json=payload, headers=self.headers)
            response_json = response.json()

            rank = np.zeros(len(texts), dtype=float)

            token_count = 0
            for t in texts:
                token_count += num_tokens_from_string(t)
            try:
                for result in response_json["results"]:
                    rank[result["index"]] = result["relevance_score"]
            except Exception as _e:
                log_exception(_e, response)

            return (
                rank,
                token_count,
            )

        except ProviderHTTPError as error:
            status = error.status_code if error.status_code is not None else "unknown"
            raise ValueError(
                f"GPUStack rerank request failed: status={status}"
            ) from None


class NovitaRerank(JinaRerank):
    _FACTORY_NAME = "NovitaAI"

    def __init__(self, key, model_name, base_url="https://api.novita.ai/v3/openai/rerank"):
        if not base_url:
            base_url = "https://api.novita.ai/v3/openai/rerank"
        super().__init__(key, model_name, base_url)


class GiteeRerank(JinaRerank):
    _FACTORY_NAME = "GiteeAI"

    def __init__(self, key, model_name, base_url="https://ai.gitee.com/v1/rerank"):
        if not base_url:
            base_url = "https://ai.gitee.com/v1/rerank"
        super().__init__(key, model_name, base_url)


class VLLMRerank(Base):
    """
    专门为VLLM部署的rerank模型设计的类

    与OpenAI_APIRerank的主要区别：
    1. 不进行文本截断（保持完整文本）
    2. 不进行分数归一化（保持原始分数）
    3. 使用直接HTTP请求而不是SDK
    """
    _FACTORY_NAME = "VLLM"

    def __init__(self, key, model_name, base_url=None):
        if not base_url:
            raise ValueError("base_url cannot be None for VLLM rerank")

        # 确保URL格式正确
        if base_url.endswith("/v1/rerank"):
            self.base_url = base_url
        elif base_url.endswith("/rerank"):
            self.base_url = base_url
        elif base_url.endswith("/v1"):
            self.base_url = f"{base_url}/rerank"
        else:
            self.base_url = f"{base_url}/rerank"

        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}" if key and key != "empty" else ""
        }
        # 移除空的Authorization头
        if not self.headers["Authorization"]:
            del self.headers["Authorization"]

        self.model_name = model_name.split("___")[0]

    def similarity(self, query: str, texts: list):
        # 不截断文本，保持完整输入
        token_count = num_tokens_from_string(query) + sum([num_tokens_from_string(t) for t in texts])

        payload = {
            "model": self.model_name,
            "query": query,
            "documents": texts,
            "top_n": len(texts),
            "return_documents": False
        }

        try:
            response = provider_post(self.base_url, json=payload, headers=self.headers)
            result = response.json()

            rank = np.zeros(len(texts), dtype=float)

            # 解析结果
            for item in result.get("results", []):
                index = item.get("index", 0)
                score = item.get("relevance_score", 0.0)
                if 0 <= index < len(texts):
                    rank[index] = score

            # 不进行归一化，直接返回原始分数
            return rank, token_count

        except Exception as _e:
            log_exception(_e)
            return np.zeros(len(texts), dtype=float), token_count
