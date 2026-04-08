#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
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

from abc import ABC, abstractmethod
from dataclasses import dataclass
import numpy as np

# 默认向量匹配返回的 top-N 数量
DEFAULT_MATCH_VECTOR_TOPN = 10
# 默认稀疏向量匹配返回的 top-N 数量
DEFAULT_MATCH_SPARSE_TOPN = 10
# 向量类型定义：可以是列表或 numpy 数组
VEC = list | np.ndarray


@dataclass
class SparseVector:
    """
    稀疏向量类

    用于表示稀疏向量，包含索引和对应的值
    """
    indices: list[int]  # 非零元素的索引列表
    values: list[float] | list[int] | None = None  # 对应索引的值列表

    def __post_init__(self):
        """初始化后验证：确保索引和值的长度一致"""
        assert (self.values is None) or (len(self.indices) == len(self.values))

    def to_dict_old(self):
        """转换为旧格式的字典表示"""
        d = {"indices": self.indices}
        if self.values is not None:
            d["values"] = self.values
        return d

    def to_dict(self):
        """转换为字典表示，索引作为键，值作为值"""
        if self.values is None:
            raise ValueError("SparseVector.values is None")
        result = {}
        for i, v in zip(self.indices, self.values):
            result[str(i)] = v
        return result

    @staticmethod
    def from_dict(d):
        """从字典创建稀疏向量实例"""
        return SparseVector(d["indices"], d.get("values"))

    def __str__(self):
        """字符串表示"""
        return f"SparseVector(indices={self.indices}{'' if self.values is None else f', values={self.values}'})"

    def __repr__(self):
        """对象表示"""
        return str(self)


class MatchTextExpr(ABC):
    """
    文本匹配表达式

    用于全文搜索的匹配表达式
    """
    def __init__(
        self,
        fields: list[str],          # 要搜索的字段列表
        matching_text: str,         # 匹配的文本内容
        topn: int,                  # 返回的 top-N 结果数量
        extra_options: dict = dict(),  # 额外的搜索选项
    ):
        self.fields = fields
        self.matching_text = matching_text
        self.topn = topn
        self.extra_options = extra_options


class MatchDenseExpr(ABC):
    """
    密集向量匹配表达式

    用于向量相似度搜索的匹配表达式
    """
    def __init__(
        self,
        vector_column_name: str,    # 向量列名
        embedding_data: VEC,        # 嵌入向量数据
        embedding_data_type: str,   # 嵌入数据类型
        distance_type: str,         # 距离计算类型（如 cosine, euclidean）
        topn: int = DEFAULT_MATCH_VECTOR_TOPN,  # 返回的 top-N 结果数量
        extra_options: dict = dict(),  # 额外的搜索选项
    ):
        self.vector_column_name = vector_column_name
        self.embedding_data = embedding_data
        self.embedding_data_type = embedding_data_type
        self.distance_type = distance_type
        self.topn = topn
        self.extra_options = extra_options


class MatchSparseExpr(ABC):
    """
    稀疏向量匹配表达式

    用于稀疏向量相似度搜索的匹配表达式
    """
    def __init__(
        self,
        vector_column_name: str,    # 向量列名
        sparse_data: SparseVector | dict,  # 稀疏向量数据
        distance_type: str,         # 距离计算类型
        topn: int,                  # 返回的 top-N 结果数量
        opt_params: dict | None = None,  # 可选参数
    ):
        self.vector_column_name = vector_column_name
        self.sparse_data = sparse_data
        self.distance_type = distance_type
        self.topn = topn
        self.opt_params = opt_params


class MatchTensorExpr(ABC):
    """
    张量匹配表达式

    用于张量数据匹配的表达式
    """
    def __init__(
        self,
        column_name: str,           # 列名
        query_data: VEC,            # 查询数据
        query_data_type: str,       # 查询数据类型
        topn: int,                  # 返回的 top-N 结果数量
        extra_option: dict | None = None,  # 额外选项
    ):
        self.column_name = column_name
        self.query_data = query_data
        self.query_data_type = query_data_type
        self.topn = topn
        self.extra_option = extra_option


class FusionExpr(ABC):
    """
    融合表达式

    用于多种搜索结果融合的表达式
    """
    def __init__(self, method: str, topn: int, fusion_params: dict | None = None):
        self.method = method        # 融合方法（如 weighted_sum）
        self.topn = topn           # 返回的 top-N 结果数量
        self.fusion_params = fusion_params  # 融合参数


# 匹配表达式的联合类型定义
MatchExpr = MatchTextExpr | MatchDenseExpr | MatchSparseExpr | MatchTensorExpr | FusionExpr

class OrderByExpr(ABC):
    """
    排序表达式类

    用于构建数据库查询的排序条件
    """
    def __init__(self):
        self.fields = list()  # 存储排序字段和方向的列表

    def asc(self, field: str):
        """添加升序排序字段"""
        self.fields.append((field, 0))  # 0 表示升序
        return self

    def desc(self, field: str):
        """添加降序排序字段"""
        self.fields.append((field, 1))  # 1 表示降序
        return self

    def fields(self):
        """获取排序字段列表"""
        return self.fields

class DocStoreConnection(ABC):
    """
    文档存储连接抽象基类

    定义了文档存储系统的标准接口，包括数据库操作、索引管理和 CRUD 操作
    """

    @abstractmethod
    def dbType(self) -> str:
        """
        返回数据库类型

        Returns:
            str: 数据库类型标识
        """
        raise NotImplementedError("Not implemented")

    @abstractmethod
    def health(self) -> dict:
        """
        返回数据库健康状态

        Returns:
            dict: 包含健康状态信息的字典
        """
        raise NotImplementedError("Not implemented")

    # ========== 索引操作 ==========

    @abstractmethod
    def createIdx(self, indexName: str, knowledgebaseId: str, vectorSize: int):
        """
        创建指定名称的索引

        Args:
            indexName: 索引名称
            knowledgebaseId: 知识库ID
            vectorSize: 向量维度大小
        """
        raise NotImplementedError("Not implemented")

    @abstractmethod
    def deleteIdx(self, indexName: str, knowledgebaseId: str):
        """
        删除指定名称的索引

        Args:
            indexName: 索引名称
            knowledgebaseId: 知识库ID
        """
        raise NotImplementedError("Not implemented")

    @abstractmethod
    def indexExist(self, indexName: str) -> bool:
        """
        检查指定名称的索引是否存在

        Args:
            indexName: 索引名称

        Returns:
            bool: 索引是否存在
        """
        raise NotImplementedError("Not implemented")

    # ========== CRUD 操作 ==========

    @abstractmethod
    def search(
        self, selectFields: list[str],      # 要返回的字段列表
            highlightFields: list[str],     # 需要高亮的字段列表
            condition: dict,                # 过滤条件字典
            matchExprs: list[MatchExpr],    # 匹配表达式列表
            orderBy: OrderByExpr,           # 排序表达式
            offset: int,                    # 偏移量（分页用）
            limit: int,                     # 限制返回数量
            indexNames: str|list[str],      # 索引名称（单个或列表）
            aggFields: list[str] = [],      # 聚合字段列表
            rank_feature: dict | None = None  # 排序特征参数
    ):
        """
        根据给定的过滤条件搜索文档，返回匹配文档的所有字段

        Args:
            selectFields: 要返回的字段列表
            highlightFields: 需要高亮显示的字段列表
            condition: 过滤条件（AND 逻辑）
            matchExprs: 匹配表达式列表（文本、向量等）
            orderBy: 排序表达式
            offset: 分页偏移量
            limit: 返回结果数量限制
            indexNames: 要搜索的索引名称
            aggFields: 需要聚合的字段列表
            rank_feature: 排序特征参数

        Returns:
            搜索结果对象
        """
        raise NotImplementedError("Not implemented")

    @abstractmethod
    def get(self, chunkId: str, indexName: str, knowledgebaseIds: list[str]) -> dict | None:
        """
        根据ID获取单个文档分块

        Args:
            chunkId: 分块ID
            indexName: 索引名称
            knowledgebaseIds: 知识库ID列表

        Returns:
            dict | None: 文档分块数据，如果不存在则返回 None
        """
        raise NotImplementedError("Not implemented")

    @abstractmethod
    def insert(self, rows: list[dict], indexName: str, knowledgebaseId: str = None) -> list[str]:
        """
        批量插入或更新文档行

        Args:
            rows: 要插入的文档行列表
            indexName: 索引名称
            knowledgebaseId: 知识库ID

        Returns:
            list[str]: 插入文档的ID列表
        """
        raise NotImplementedError("Not implemented")

    @abstractmethod
    def update(self, condition: dict, newValue: dict, indexName: str, knowledgebaseId: str) -> bool:
        """
        根据条件更新文档行

        Args:
            condition: 更新条件（AND 逻辑）
            newValue: 新的字段值
            indexName: 索引名称
            knowledgebaseId: 知识库ID

        Returns:
            bool: 更新是否成功
        """
        raise NotImplementedError("Not implemented")

    @abstractmethod
    def delete(self, condition: dict, indexName: str, knowledgebaseId: str) -> int:
        """
        根据条件删除文档行

        Args:
            condition: 删除条件（AND 逻辑）
            indexName: 索引名称
            knowledgebaseId: 知识库ID

        Returns:
            int: 删除的文档数量
        """
        raise NotImplementedError("Not implemented")

    # ========== 搜索结果辅助函数 ==========

    @abstractmethod
    def getTotal(self, res):
        """
        从搜索结果中获取总数量

        Args:
            res: 搜索结果对象

        Returns:
            int: 匹配的文档总数
        """
        raise NotImplementedError("Not implemented")

    @abstractmethod
    def getChunkIds(self, res):
        """
        从搜索结果中获取分块ID列表

        Args:
            res: 搜索结果对象

        Returns:
            list[str]: 分块ID列表
        """
        raise NotImplementedError("Not implemented")

    @abstractmethod
    def getFields(self, res, fields: list[str]) -> dict[str, dict]:
        """
        从搜索结果中获取指定字段的数据

        Args:
            res: 搜索结果对象
            fields: 要获取的字段列表

        Returns:
            dict[str, dict]: 以分块ID为键，字段数据为值的字典
        """
        raise NotImplementedError("Not implemented")

    @abstractmethod
    def getHighlight(self, res, keywords: list[str], fieldnm: str):
        """
        从搜索结果中获取高亮信息

        Args:
            res: 搜索结果对象
            keywords: 关键词列表
            fieldnm: 字段名称

        Returns:
            高亮信息数据
        """
        raise NotImplementedError("Not implemented")

    @abstractmethod
    def getAggregation(self, res, fieldnm: str):
        """
        从搜索结果中获取聚合信息

        Args:
            res: 搜索结果对象
            fieldnm: 聚合字段名称

        Returns:
            聚合结果数据
        """
        raise NotImplementedError("Not implemented")

    # ========== SQL 操作 ==========

    @abstractmethod
    def sql(sql: str, fetch_size: int, format: str):
        """
        执行由文本转SQL生成的SQL语句

        Args:
            sql: 要执行的SQL语句
            fetch_size: 获取的结果数量
            format: 返回格式

        Returns:
            SQL执行结果
        """
        raise NotImplementedError("Not implemented")
