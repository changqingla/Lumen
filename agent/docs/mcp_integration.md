# MCP (Model Context Protocol) 集成架构文档

## 概述

本项目实现了一个完整的 MCP 客户端系统，用于连接和调用外部 MCP 服务器提供的工具。该系统采用分层架构设计，包含配置管理、连接管理、连接池、工具适配和工具注册等模块。

## 架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        ReAct Agent                               │
│                    (智能体决策层)                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      ToolRegistry                                │
│                    (工具注册中心)                                 │
│         管理原生工具 + MCP工具，提供统一访问接口                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    MCPToolAdapter                                │
│                   (MCP工具适配器)                                 │
│         将MCP工具转换为LangChain兼容格式                          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   MCPClientManager                               │
│                  (MCP客户端管理器)                                │
│         管理多个MCP服务器连接池，统一工具发现和调用                  │
└─────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│ MCPConnectionPool│ │ MCPConnectionPool│ │ MCPConnectionPool│
│   (arxiv服务器)   │ │  (amap服务器)    │ │   (其他服务器)    │
└──────────────────┘ └──────────────────┘ └──────────────────┘
              │               │               │
              ▼               ▼               ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│    MCPClient     │ │    MCPClient     │ │    MCPClient     │
│    MCPClient     │ │    MCPClient     │ │    MCPClient     │
│    MCPClient     │ │    MCPClient     │ │    MCPClient     │
└──────────────────┘ └──────────────────┘ └──────────────────┘
              │               │               │
              ▼               ▼               ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│  MCP Server      │ │  MCP Server      │ │  MCP Server      │
│  (外部进程)       │ │  (外部进程)       │ │  (外部进程)       │
└──────────────────┘ └──────────────────┘ └──────────────────┘
```

---

## 模块详解


## 1. 数据模型层 (models.py)

### 文件位置
`agent_system/src/mcp/models.py`

### 功能说明
定义 MCP 集成中使用的核心数据结构，包括服务器状态、工具定义和调用结果。

### 核心类

#### ServerStatus (枚举)
```python
class ServerStatus(Enum):
    DISCONNECTED = "disconnected"  # 未连接
    CONNECTING = "connecting"      # 连接中
    CONNECTED = "connected"        # 已连接
    ERROR = "error"                # 错误状态
```

#### MCPTool (数据类)
```python
@dataclass
class MCPTool:
    name: str                      # 工具名称
    description: str               # 工具描述
    input_schema: Dict[str, Any]   # JSON Schema 输入参数定义
    server_id: str                 # 所属服务器ID
```

#### MCPToolResult (数据类)
```python
@dataclass
class MCPToolResult:
    success: bool                  # 调用是否成功
    content: Any                   # 返回内容
    error: Optional[str] = None    # 错误信息
    execution_time: float = 0.0    # 执行耗时(秒)
```

### 使用的核心库
- `dataclasses`: Python 标准库，用于创建数据类
- `enum.Enum`: Python 标准库，用于定义枚举类型

---

## 2. 配置管理层 (config.py)

### 文件位置
`agent_system/src/mcp/config.py`

### 功能说明
负责加载、验证和管理 MCP 服务器配置。从 JSON 配置文件读取服务器定义。

### 配置文件格式
配置文件位于 `agent_system/config/mcp_servers.json`:
```json
{
  "mcpServers": {
    "arxiv": {
      "command": "uvx",
      "args": ["arxiv-mcp-server@latest"],
      "env": {"FASTMCP_LOG_LEVEL": "ERROR"},
      "timeout": 60,
      "poolMinSize": 3,
      "poolMaxSize": 5,
      "disabled": false
    }
  }
}
```

### 核心类

#### MCPServerConfig (数据类)
```python
@dataclass
class MCPServerConfig:
    server_id: str          # 服务器唯一标识
    command: str            # 启动命令 (如 "uvx", "npx")
    args: List[str]         # 命令参数
    env: Dict[str, str]     # 环境变量
    disabled: bool          # 是否禁用
    timeout: float          # 超时时间(秒)
    retry_count: int        # 重试次数
    pool_min_size: int      # 连接池最小连接数
    pool_max_size: int      # 连接池最大连接数
```

#### MCPConfigManager
```python
class MCPConfigManager:
    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self.configs: Dict[str, MCPServerConfig] = {}
    
    def load_config(self) -> Dict[str, MCPServerConfig]:
        """加载并解析配置文件"""
        # 1. 检查文件是否存在
        # 2. 读取 JSON 内容
        # 3. 验证每个服务器配置
        # 4. 创建 MCPServerConfig 对象
    
    def validate_config(self, config: Dict) -> List[str]:
        """验证配置有效性，返回错误列表"""
        # 检查必填字段: command
        # 验证字段类型: args(list), env(dict), disabled(bool)
        # 验证数值范围: timeout > 0, retryCount >= 0
    
    def get_enabled_configs(self) -> Dict[str, MCPServerConfig]:
        """获取所有启用的服务器配置"""
```

### 工作流程
```
1. 初始化 MCPConfigManager(config_path)
2. 调用 load_config()
   ├── 读取 JSON 文件
   ├── 遍历 mcpServers 字典
   ├── 对每个服务器调用 validate_config()
   ├── 创建 MCPServerConfig 对象
   └── 存储到 self.configs
3. 调用 get_enabled_configs() 获取未禁用的服务器
```

### 使用的核心库
- `json`: Python 标准库，解析 JSON 配置
- `pathlib.Path`: Python 标准库，路径处理
- `dataclasses`: Python 标准库，数据类定义

---


## 3. MCP客户端层 (client.py)

### 文件位置
`agent_system/src/mcp/client.py`

### 功能说明
实现与单个 MCP 服务器的连接管理和工具调用。这是最底层的连接单元。

### 核心类

#### MCPClient
```python
class MCPClient:
    def __init__(self, server_id: str, config: MCPServerConfig):
        self.server_id = server_id
        self.config = config
        self.session: Optional[ClientSession] = None
        self.tools: List[MCPTool] = []
        self.status: ServerStatus = ServerStatus.DISCONNECTED
        self._context_manager = None
```

### 核心方法

#### connect() - 建立连接
```python
async def connect(self, timeout: Optional[float] = None) -> bool:
    """
    建立与MCP服务器的连接
    
    工作流程:
    1. 设置状态为 CONNECTING
    2. 调用 _establish_connection() 建立实际连接
    3. 如果失败，使用指数退避重试 (1s, 2s, 4s...)
    4. 成功后设置状态为 CONNECTED
    5. 失败后设置状态为 ERROR 并抛出异常
    """
```

#### _establish_connection() - 实际连接逻辑
```python
async def _establish_connection(self, timeout: float) -> None:
    """
    核心连接逻辑，使用 MCP SDK
    
    关键步骤:
    1. 创建 StdioServerParameters (命令、参数、环境变量)
    2. 使用 stdio_client() 创建上下文管理器
    3. 获取读写流 (read_stream, write_stream)
    4. 创建 ClientSession 并初始化
    """
    server_params = StdioServerParameters(
        command=self.config.command,      # 如 "uvx"
        args=self.config.args,            # 如 ["arxiv-mcp-server@latest"]
        env=self.config.env,              # 环境变量
    )
    
    self._context_manager = stdio_client(server_params)
    
    async with asyncio.timeout(timeout):
        self._read_stream, self._write_stream = await self._context_manager.__aenter__()
        self.session = ClientSession(self._read_stream, self._write_stream)
        await self.session.__aenter__()
        await self.session.initialize()
```

#### discover_tools() - 发现工具
```python
async def discover_tools(self) -> List[MCPTool]:
    """
    从服务器发现可用工具
    
    工作流程:
    1. 检查连接状态
    2. 调用 session.list_tools() 获取工具列表
    3. 将 MCP SDK 的工具对象转换为 MCPTool 数据类
    4. 返回工具列表
    """
    result = await self.session.list_tools()
    
    self.tools = [
        MCPTool(
            name=tool.name,
            description=tool.description or "",
            input_schema=tool.inputSchema,
            server_id=self.server_id,
        )
        for tool in result.tools
    ]
```

#### call_tool() - 调用工具
```python
async def call_tool(
    self,
    tool_name: str,
    arguments: Dict[str, Any],
    timeout: Optional[float] = None
) -> MCPToolResult:
    """
    调用MCP服务器上的工具
    
    工作流程:
    1. 检查连接状态
    2. 使用 asyncio.timeout 设置超时
    3. 调用 session.call_tool(tool_name, arguments)
    4. 提取返回内容 (处理单个/多个结果)
    5. 返回 MCPToolResult
    """
    async with asyncio.timeout(timeout):
        result = await self.session.call_tool(tool_name, arguments)
    
    # 提取内容
    content = None
    if result.content:
        if len(result.content) == 1:
            content = result.content[0].text
        else:
            content = [item.text for item in result.content]
    
    return MCPToolResult(
        success=not result.isError,
        content=content,
        execution_time=execution_time
    )
```

#### disconnect() - 断开连接
```python
async def disconnect(self) -> None:
    """
    断开与MCP服务器的连接
    
    工作流程:
    1. 关闭 ClientSession
    2. 退出 stdio_client 上下文管理器
    3. 清理资源，重置状态
    """
```

### 异常类
```python
class MCPConnectionError(Exception):
    """连接错误基类"""
    def __init__(self, server_id: str, message: str, retry_count: int = 0)

class MCPTimeoutError(MCPConnectionError):
    """超时错误"""

class MCPToolError(Exception):
    """工具调用错误"""
    def __init__(self, tool_name: str, message: str, original_error: Optional[Exception])
```

### 使用的核心库
- **mcp**: 官方 MCP Python SDK
  - `ClientSession`: MCP 客户端会话
  - `StdioServerParameters`: 标准输入输出服务器参数
  - `stdio_client`: 创建 stdio 客户端连接
- **asyncio**: Python 异步编程
  - `asyncio.timeout()`: 异步超时控制
  - `asyncio.sleep()`: 异步等待(用于重试退避)

---


## 4. 连接池层 (connection_pool.py)

### 文件位置
`agent_system/src/mcp/connection_pool.py`

### 功能说明
为单个 MCP 服务器维护连接池，支持并发访问。解决多个请求同时需要调用同一服务器工具的问题。

### 核心常量
```python
DEFAULT_POOL_SIZE = 3       # 默认最小连接数
DEFAULT_MAX_POOL_SIZE = 5   # 默认最大连接数
DEFAULT_ACQUIRE_TIMEOUT = 30.0  # 获取连接超时时间
```

### 核心类

#### PooledConnection (数据类)
```python
@dataclass
class PooledConnection:
    client: MCPClient           # MCP客户端实例
    in_use: bool = False        # 是否正在使用
    created_at: float           # 创建时间戳
    last_used_at: float         # 最后使用时间戳
    use_count: int = 0          # 使用次数统计
```

#### MCPConnectionPool
```python
class MCPConnectionPool:
    def __init__(
        self,
        server_id: str,
        config: MCPServerConfig,
        min_size: int = DEFAULT_POOL_SIZE,
        max_size: int = DEFAULT_MAX_POOL_SIZE
    ):
        self.server_id = server_id
        self.config = config
        self.min_size = min_size
        self.max_size = max_size
        self.connections: List[PooledConnection] = []
        self.tools: List[MCPTool] = []
        self._lock = asyncio.Lock()              # 互斥锁
        self._condition = asyncio.Condition(self._lock)  # 条件变量
        self._initialized = False
        self._closed = False
```

### 核心方法

#### initialize() - 初始化连接池
```python
async def initialize(self) -> bool:
    """
    初始化连接池
    
    工作流程:
    1. 获取锁，检查是否已初始化
    2. 并发创建 min_size 个连接
    3. 从第一个连接发现工具列表
    4. 设置 _initialized = True
    """
    async with self._lock:
        # 并发创建初始连接
        tasks = [self._create_connection(i) for i in range(self.min_size)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 从第一个连接发现工具
        if self.connections:
            raw_tools = await self.connections[0].client.discover_tools()
            self.tools = [MCPTool(...) for t in raw_tools]
```

#### _create_connection() - 创建单个连接
```python
async def _create_connection(self, index: int = 0) -> Optional[PooledConnection]:
    """
    创建新的池化连接
    
    工作流程:
    1. 创建 MCPClient 实例
    2. 调用 client.connect() 建立连接
    3. 包装为 PooledConnection
    4. 添加到 connections 列表
    """
    client = MCPClient(f"{self.server_id}_{index}", self.config)
    await client.connect()
    pooled = PooledConnection(client=client)
    self.connections.append(pooled)
    return pooled
```

#### acquire() - 获取连接
```python
async def acquire(self, timeout: float = DEFAULT_ACQUIRE_TIMEOUT) -> Optional[MCPClient]:
    """
    从池中获取一个可用连接
    
    工作流程:
    1. 检查池是否已关闭
    2. 获取条件变量锁
    3. 循环查找可用连接:
       a. 遍历连接列表，找到 in_use=False 且状态为 CONNECTED 的连接
       b. 如果找到，标记为使用中并返回
       c. 如果未找到且连接数 < max_size，创建新连接
       d. 如果达到上限，等待其他连接释放
    4. 超时返回 None
    """
    async with self._condition:
        while True:
            # 查找可用连接
            for pooled in self.connections:
                if not pooled.in_use and pooled.client.status == ServerStatus.CONNECTED:
                    pooled.in_use = True
                    pooled.last_used_at = time.time()
                    pooled.use_count += 1
                    return pooled.client
            
            # 尝试创建新连接
            if len(self.connections) < self.max_size:
                pooled = await self._create_connection(len(self.connections))
                if pooled:
                    pooled.in_use = True
                    return pooled.client
            
            # 等待连接释放
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            await asyncio.wait_for(self._condition.wait(), timeout=remaining)
```

#### release() - 释放连接
```python
async def release(self, client: MCPClient) -> None:
    """
    将连接归还到池中
    
    工作流程:
    1. 获取条件变量锁
    2. 找到对应的 PooledConnection
    3. 设置 in_use = False
    4. 更新 last_used_at
    5. 通知等待的协程 (condition.notify())
    """
    async with self._condition:
        for pooled in self.connections:
            if pooled.client is client:
                pooled.in_use = False
                pooled.last_used_at = time.time()
                self._condition.notify()  # 唤醒等待的协程
                return
```

#### call_tool() - 使用池化连接调用工具
```python
async def call_tool(
    self,
    tool_name: str,
    arguments: Dict[str, Any],
    timeout: Optional[float] = None
) -> MCPToolResult:
    """
    使用池化连接调用工具 (自动获取和释放连接)
    
    工作流程:
    1. 调用 acquire() 获取连接
    2. 调用 client.call_tool() 执行工具
    3. 在 finally 中调用 release() 释放连接
    """
    client = await self.acquire()
    if not client:
        return MCPToolResult(success=False, error="Failed to acquire connection")
    
    try:
        return await client.call_tool(tool_name, arguments, timeout)
    finally:
        await self.release(client)
```

#### close() - 关闭连接池
```python
async def close(self) -> None:
    """
    关闭所有连接
    
    工作流程:
    1. 获取锁
    2. 设置 _closed = True
    3. 遍历所有连接，调用 disconnect()
    4. 清空连接列表和工具列表
    """
```

#### get_stats() - 获取统计信息
```python
def get_stats(self) -> Dict[str, Any]:
    """返回连接池统计信息"""
    return {
        "server_id": self.server_id,
        "min_size": self.min_size,
        "max_size": self.max_size,
        "total_connections": len(self.connections),
        "in_use": sum(1 for p in self.connections if p.in_use),
        "available": connected - in_use,
        "tools_count": len(self.tools),
    }
```

### 使用的核心库
- **asyncio**: Python 异步编程
  - `asyncio.Lock()`: 异步互斥锁
  - `asyncio.Condition()`: 异步条件变量，用于等待/通知机制
  - `asyncio.gather()`: 并发执行多个协程
  - `asyncio.wait_for()`: 带超时的等待
- **time**: Python 标准库
  - `time.time()`: 获取时间戳

---


## 5. 客户端管理器层 (client_manager.py)

### 文件位置
`agent_system/src/mcp/client_manager.py`

### 功能说明
管理多个 MCP 服务器的连接池，提供统一的工具发现和调用接口。是 MCP 集成的核心协调器。

### 核心类

#### MCPClientManager
```python
class MCPClientManager:
    def __init__(
        self,
        config_path: str = "config/mcp_servers.json",
        pool_min_size: int = DEFAULT_POOL_SIZE,
        pool_max_size: int = DEFAULT_MAX_POOL_SIZE
    ):
        self.config_path = config_path
        self.config_manager = MCPConfigManager(config_path)
        self.pool_min_size = pool_min_size
        self.pool_max_size = pool_max_size
        self.pools: Dict[str, MCPConnectionPool] = {}      # 服务器ID -> 连接池
        self.tools: Dict[str, MCPTool] = {}                # 工具名 -> 工具对象
        self._tool_to_server: Dict[str, str] = {}          # 工具名 -> 服务器ID
```

### 核心方法

#### initialize() - 初始化所有服务器
```python
async def initialize(self) -> None:
    """
    初始化所有配置的MCP服务器连接池
    
    工作流程:
    1. 加载配置文件
    2. 获取所有启用的服务器配置
    3. 并发初始化所有连接池
    4. 记录成功/失败数量
    """
    self.config_manager.load_config()
    enabled_configs = self.config_manager.get_enabled_configs()
    
    # 并发初始化所有池
    tasks = [
        self._init_pool(server_id, config)
        for server_id, config in enabled_configs.items()
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
```

#### _init_pool() - 初始化单个服务器池
```python
async def _init_pool(self, server_id: str, config: MCPServerConfig) -> bool:
    """
    初始化单个服务器的连接池
    
    工作流程:
    1. 创建 MCPConnectionPool 实例
    2. 调用 pool.initialize()
    3. 如果成功，存储到 self.pools
    4. 注册该池发现的所有工具
    """
    pool = MCPConnectionPool(
        server_id=server_id,
        config=config,
        min_size=config.pool_min_size or self.pool_min_size,
        max_size=config.pool_max_size or self.pool_max_size
    )
    
    if await pool.initialize():
        self.pools[server_id] = pool
        for tool in pool.tools:
            self._register_tool(tool)
        return True
    return False
```

#### _register_tool() - 注册工具
```python
def _register_tool(self, tool: MCPTool) -> None:
    """
    注册工具，处理名称冲突
    
    工作流程:
    1. 检查工具名是否已存在
    2. 如果存在且来自不同服务器，添加服务器前缀
    3. 存储到 tools 和 _tool_to_server 字典
    """
    tool_name = tool.name
    
    if tool_name in self.tools:
        existing_server = self._tool_to_server.get(tool_name)
        if existing_server != tool.server_id:
            tool_name = f"{tool.server_id}_{tool.name}"  # 添加前缀避免冲突
    
    self.tools[tool_name] = tool
    self._tool_to_server[tool_name] = tool.server_id
```

#### call_tool() - 调用工具
```python
async def call_tool(
    self,
    tool_name: str,
    arguments: Dict[str, Any]
) -> MCPToolResult:
    """
    调用MCP工具
    
    工作流程:
    1. 查找工具对应的服务器ID
    2. 获取该服务器的连接池
    3. 调用 pool.call_tool() 执行
    """
    if tool_name not in self.tools:
        return MCPToolResult(success=False, error=f"Tool '{tool_name}' not found")
    
    server_id = self._tool_to_server[tool_name]
    pool = self.pools[server_id]
    
    tool = self.tools[tool_name]
    return await pool.call_tool(tool.name, arguments)
```

#### 其他方法
```python
def get_available_tools(self) -> List[MCPTool]:
    """获取所有可用工具"""
    return list(self.tools.values())

def get_tool(self, tool_name: str) -> Optional[MCPTool]:
    """根据名称获取工具"""
    return self.tools.get(tool_name)

def get_connected_servers(self) -> List[str]:
    """获取已连接的服务器ID列表"""

def get_pool_stats(self) -> Dict[str, Any]:
    """获取所有连接池的统计信息"""

async def disconnect_server(self, server_id: str) -> None:
    """断开指定服务器"""

async def disconnect_all(self) -> None:
    """断开所有服务器"""
```

### 使用的核心库
- **asyncio**: Python 异步编程
  - `asyncio.gather()`: 并发执行多个初始化任务

---


## 6. 工具适配器层 (tool_adapter.py)

### 文件位置
`agent_system/src/mcp/tool_adapter.py`

### 功能说明
将 MCP 工具转换为 LangChain 兼容的工具格式，使其可以被 ReAct 智能体使用。

### 核心函数

#### create_input_model() - 动态创建输入模型
```python
def create_input_model(tool_name: str, input_schema: Dict[str, Any]) -> Type[BaseModel]:
    """
    根据 JSON Schema 动态创建 Pydantic 模型
    
    工作流程:
    1. 解析 JSON Schema 的 properties 和 required 字段
    2. 将 JSON 类型映射到 Python 类型
    3. 使用 pydantic.create_model() 动态创建模型类
    
    示例:
    输入 Schema:
    {
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {"type": "integer", "description": "最大结果数"}
        },
        "required": ["query"]
    }
    
    输出: 动态创建的 Pydantic 模型类
    """
    properties = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))
    
    fields = {}
    for prop_name, prop_schema in properties.items():
        prop_type = _json_type_to_python(prop_schema.get("type", "string"))
        description = prop_schema.get("description", "")
        default = ... if prop_name in required else None
        
        fields[prop_name] = (
            Optional[prop_type] if prop_name not in required else prop_type,
            Field(default=default, description=description)
        )
    
    return create_model(f"{tool_name}Input", **fields)
```

#### _json_type_to_python() - 类型映射
```python
def _json_type_to_python(json_type: str) -> type:
    """JSON Schema 类型到 Python 类型的映射"""
    type_mapping = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    return type_mapping.get(json_type, str)
```

### 核心类

#### MCPToolAdapter
```python
class MCPToolAdapter(BaseTool):
    """
    MCP工具到LangChain工具的适配器
    
    继承自 langchain_core.tools.BaseTool
    """
    name: str = ""
    description: str = ""
    original_tool_name: str = ""
    input_schema_dict: Dict[str, Any] = Field(default_factory=dict)
    _mcp_client_manager: Optional["MCPClientManager"] = None
    
    def __init__(
        self,
        name: str,
        description: str,
        mcp_client_manager: "MCPClientManager",
        original_tool_name: str,
        input_schema: Dict[str, Any],
    ):
        # 动态创建参数模型
        args_schema = create_input_model(name, input_schema)
        
        super().__init__(
            name=name,
            description=description,
            args_schema=args_schema,
            ...
        )
        self._mcp_client_manager = mcp_client_manager
```

### 核心方法

#### _run() - 同步执行
```python
def _run(self, **kwargs) -> str:
    """
    同步工具执行 (LangChain 要求实现)
    
    工作流程:
    1. 检查是否在异步上下文中
    2. 如果是，使用线程池执行异步方法
    3. 如果不是，直接运行异步方法
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 在异步上下文中，使用线程池
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, self._arun(**kwargs))
                return future.result()
        else:
            return loop.run_until_complete(self._arun(**kwargs))
    except RuntimeError:
        return asyncio.run(self._arun(**kwargs))
```

#### _arun() - 异步执行
```python
async def _arun(self, **kwargs) -> str:
    """
    异步工具执行
    
    工作流程:
    1. 检查 client_manager 是否初始化
    2. 序列化参数 (类型转换)
    3. 调用 mcp_client_manager.call_tool()
    4. 格式化返回结果为 JSON 字符串
    """
    # 序列化参数
    serialized_args = self._serialize_arguments(kwargs)
    
    # 调用工具
    result = await self._mcp_client_manager.call_tool(self.name, serialized_args)
    
    # 格式化响应
    return self._format_response(result)
```

#### _serialize_arguments() - 参数序列化
```python
def _serialize_arguments(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    根据 Schema 序列化参数
    
    工作流程:
    1. 遍历参数，过滤未知参数
    2. 根据 Schema 定义的类型转换值
    3. 检查必填字段
    """
    schema_props = self.input_schema_dict.get("properties", {})
    required = set(self.input_schema_dict.get("required", []))
    
    serialized = {}
    for key, value in arguments.items():
        if key not in schema_props:
            continue
        prop_schema = schema_props[key]
        expected_type = prop_schema.get("type", "string")
        serialized[key] = self._convert_value(value, expected_type)
    
    # 检查必填字段
    missing = required - set(serialized.keys())
    if missing:
        raise ValueError(f"Missing required arguments: {missing}")
    
    return serialized
```

#### _format_response() - 格式化响应
```python
def _format_response(self, result: MCPToolResult) -> str:
    """
    将 MCPToolResult 格式化为 JSON 字符串
    
    工作流程:
    1. 如果成功，返回内容的 JSON 表示
    2. 如果失败，返回错误信息的 JSON
    """
    if result.success:
        if isinstance(result.content, str):
            return result.content
        return json.dumps(result.content, ensure_ascii=False, indent=2)
    else:
        return json.dumps({"error": result.error})
```

### 工厂函数

#### create_mcp_tools() - 批量创建适配器
```python
def create_mcp_tools(mcp_client_manager: "MCPClientManager") -> list[MCPToolAdapter]:
    """
    从 MCPClientManager 创建所有 LangChain 工具
    
    工作流程:
    1. 获取所有可用的 MCP 工具
    2. 为每个工具创建 MCPToolAdapter
    3. 返回适配器列表
    """
    tools = []
    for tool in mcp_client_manager.get_available_tools():
        adapter = MCPToolAdapter(
            name=tool.name,
            description=tool.description,
            mcp_client_manager=mcp_client_manager,
            original_tool_name=tool.name,
            input_schema=tool.input_schema,
        )
        tools.append(adapter)
    return tools
```

### 使用的核心库
- **langchain_core.tools.BaseTool**: LangChain 工具基类
- **pydantic**: 数据验证库
  - `BaseModel`: 模型基类
  - `Field`: 字段定义
  - `create_model()`: 动态创建模型
- **json**: JSON 序列化
- **asyncio**: 异步编程
- **concurrent.futures**: 线程池执行器

---


## 7. 工具注册中心 (registry.py)

### 文件位置
`agent_system/src/tools/registry.py`

### 功能说明
统一管理原生工具和 MCP 工具，为 ReAct 智能体提供完整的工具列表。

### 核心类

#### ToolRegistry
```python
class ToolRegistry:
    """
    工具注册中心
    
    管理两类工具:
    1. 原生工具 (native_tools): 直接用 LangChain 实现的工具
    2. MCP工具 (mcp_tools): 通过 MCP 协议调用的外部工具
    """
    def __init__(self):
        self.native_tools: Dict[str, BaseTool] = {}           # 原生工具
        self.mcp_tools: Dict[str, "MCPToolAdapter"] = {}      # MCP工具
        self._tool_to_server: Dict[str, str] = {}             # 工具名 -> 服务器ID
        self._server_tools: Dict[str, Set[str]] = {}          # 服务器ID -> 工具名集合
```

### 核心方法

#### 原生工具管理
```python
def register_native_tool(self, tool: BaseTool) -> None:
    """注册单个原生工具"""
    self.native_tools[tool.name] = tool

def register_native_tools(self, tools: List[BaseTool]) -> None:
    """批量注册原生工具"""
    for tool in tools:
        self.register_native_tool(tool)

def unregister_native_tool(self, tool_name: str) -> bool:
    """注销原生工具"""
```

#### MCP工具管理
```python
def register_mcp_tools(
    self,
    tools: List["MCPToolAdapter"],
    server_id: str
) -> List[str]:
    """
    注册来自某个MCP服务器的所有工具
    
    工作流程:
    1. 初始化服务器的工具集合
    2. 逐个注册工具，处理名称冲突
    3. 返回实际注册的工具名列表
    """
    registered_names = []
    
    if server_id not in self._server_tools:
        self._server_tools[server_id] = set()
    
    for tool in tools:
        registered_name = self._register_single_mcp_tool(tool, server_id)
        registered_names.append(registered_name)
    
    return registered_names

def _register_single_mcp_tool(
    self,
    tool: "MCPToolAdapter",
    server_id: str
) -> str:
    """
    注册单个MCP工具，处理名称冲突
    
    冲突处理策略:
    1. 与原生工具冲突: 添加服务器前缀
    2. 与其他MCP工具冲突: 添加服务器前缀
    """
    original_name = tool.name
    registered_name = original_name
    
    # 检查与原生工具的冲突
    if original_name in self.native_tools:
        registered_name = f"{server_id}_{original_name}"
    # 检查与其他MCP工具的冲突
    elif original_name in self.mcp_tools:
        existing_server = self._tool_to_server.get(original_name)
        if existing_server != server_id:
            registered_name = f"{server_id}_{original_name}"
    
    self.mcp_tools[registered_name] = tool
    self._tool_to_server[registered_name] = server_id
    self._server_tools[server_id].add(registered_name)
    
    return registered_name

def unregister_mcp_tools(self, server_id: str) -> List[str]:
    """注销某个服务器的所有工具"""
```

#### 工具查询
```python
def get_all_tools(self) -> List[BaseTool]:
    """获取所有工具 (原生 + MCP)"""
    all_tools = []
    all_tools.extend(self.native_tools.values())
    all_tools.extend(self.mcp_tools.values())
    return all_tools

def get_tool(self, tool_name: str) -> Optional[BaseTool]:
    """根据名称获取工具"""
    if tool_name in self.native_tools:
        return self.native_tools[tool_name]
    if tool_name in self.mcp_tools:
        return self.mcp_tools[tool_name]
    return None

def has_tool(self, tool_name: str) -> bool:
    """检查工具是否存在"""

def get_tool_count(self) -> int:
    """获取工具总数"""

def get_server_tools(self, server_id: str) -> List["MCPToolAdapter"]:
    """获取某个服务器的所有工具"""

def get_tool_descriptions(self) -> str:
    """获取所有工具的描述文本"""
```

### 全局注册表
```python
_global_registry: Optional[ToolRegistry] = None

def get_tool_registry() -> ToolRegistry:
    """获取全局工具注册表实例 (单例模式)"""
    global _global_registry
    if _global_registry is None:
        _global_registry = ToolRegistry()
    return _global_registry

def reset_tool_registry() -> None:
    """重置全局工具注册表"""
    global _global_registry
    _global_registry = ToolRegistry()
```

### 使用的核心库
- **langchain_core.tools.BaseTool**: LangChain 工具基类
- **typing**: 类型注解

---


## 8. 完整工作流程

### 初始化流程

```
┌─────────────────────────────────────────────────────────────────┐
│                        应用启动                                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  1. MCPClientManager.initialize()                               │
│     ├── MCPConfigManager.load_config()                          │
│     │   └── 读取 mcp_servers.json                               │
│     │   └── 验证配置                                            │
│     │   └── 创建 MCPServerConfig 对象                           │
│     │                                                           │
│     ├── 获取启用的服务器配置                                      │
│     │                                                           │
│     └── 并发初始化所有连接池                                      │
│         └── MCPConnectionPool.initialize()                      │
│             ├── 创建 min_size 个 MCPClient                      │
│             │   └── MCPClient.connect()                         │
│             │       └── stdio_client() 启动外部进程              │
│             │       └── ClientSession.initialize()              │
│             │                                                   │
│             └── MCPClient.discover_tools()                      │
│                 └── session.list_tools()                        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  2. 注册工具到 ToolRegistry                                      │
│     ├── create_mcp_tools() 创建适配器                            │
│     └── registry.register_mcp_tools()                           │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  3. 智能体获取工具列表                                           │
│     └── registry.get_all_tools()                                │
└─────────────────────────────────────────────────────────────────┘
```

### 工具调用流程

```
┌─────────────────────────────────────────────────────────────────┐
│                   ReAct Agent 决定调用工具                        │
│                   工具名: "search_papers"                        │
│                   参数: {"query": "transformer", "max_results": 5}│
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  1. MCPToolAdapter._run() / _arun()                             │
│     ├── 序列化参数 (_serialize_arguments)                        │
│     │   └── 类型转换，验证必填字段                                │
│     │                                                           │
│     └── 调用 mcp_client_manager.call_tool()                     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  2. MCPClientManager.call_tool()                                │
│     ├── 查找工具对应的服务器ID                                    │
│     └── 获取对应的连接池                                         │
│     └── 调用 pool.call_tool()                                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  3. MCPConnectionPool.call_tool()                               │
│     ├── acquire() 获取可用连接                                   │
│     │   ├── 查找空闲连接                                         │
│     │   ├── 或创建新连接 (如果未达上限)                           │
│     │   └── 或等待连接释放                                       │
│     │                                                           │
│     ├── client.call_tool() 执行调用                              │
│     │                                                           │
│     └── release() 释放连接                                       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  4. MCPClient.call_tool()                                       │
│     ├── session.call_tool(tool_name, arguments)                 │
│     │   └── 通过 stdio 发送请求到 MCP 服务器                      │
│     │   └── 等待响应                                             │
│     │                                                           │
│     └── 构造 MCPToolResult                                       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  5. MCPToolAdapter._format_response()                           │
│     └── 将结果格式化为 JSON 字符串返回给智能体                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 9. 依赖库汇总

### Python 标准库
| 库名 | 用途 |
|------|------|
| `asyncio` | 异步编程，协程管理 |
| `json` | JSON 解析和序列化 |
| `logging` | 日志记录 |
| `time` | 时间戳获取 |
| `pathlib` | 路径处理 |
| `dataclasses` | 数据类定义 |
| `enum` | 枚举类型 |
| `typing` | 类型注解 |
| `concurrent.futures` | 线程池执行器 |

### 第三方库
| 库名 | 版本 | 用途 |
|------|------|------|
| `mcp` | latest | MCP 官方 Python SDK |
| `langchain_core` | - | LangChain 核心库，提供 BaseTool |
| `pydantic` | v2 | 数据验证，动态模型创建 |

### MCP SDK 核心组件
| 组件 | 用途 |
|------|------|
| `ClientSession` | MCP 客户端会话管理 |
| `StdioServerParameters` | stdio 服务器参数配置 |
| `stdio_client` | 创建 stdio 客户端连接 |

---

## 10. 配置示例

### mcp_servers.json 完整示例
```json
{
  "mcpServers": {
    "arxiv": {
      "command": "uvx",
      "args": ["arxiv-mcp-server@latest"],
      "env": {
        "FASTMCP_LOG_LEVEL": "ERROR"
      },
      "timeout": 60,
      "retryCount": 3,
      "poolMinSize": 3,
      "poolMaxSize": 5,
      "disabled": false
    },
    "amap-maps": {
      "command": "npx",
      "args": ["-y", "@amap/amap-maps-mcp-server"],
      "env": {
        "AMAP_MAPS_API_KEY": "your-api-key"
      },
      "timeout": 60,
      "poolMinSize": 2,
      "poolMaxSize": 3,
      "disabled": false
    }
  }
}
```

### 配置字段说明
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `command` | string | ✓ | 启动命令 (uvx/npx/python等) |
| `args` | string[] | - | 命令参数 |
| `env` | object | - | 环境变量 |
| `timeout` | number | - | 超时时间(秒)，默认30 |
| `retryCount` | number | - | 重试次数，默认3 |
| `poolMinSize` | number | - | 最小连接数，默认3 |
| `poolMaxSize` | number | - | 最大连接数，默认5 |
| `disabled` | boolean | - | 是否禁用，默认false |

---

## 11. 错误处理

### 异常层次
```
Exception
├── MCPConnectionError          # 连接错误
│   └── MCPTimeoutError         # 超时错误
└── MCPToolError                # 工具调用错误
```

### 错误处理策略
1. **连接失败**: 指数退避重试 (1s, 2s, 4s...)
2. **工具调用超时**: 返回 MCPToolResult(success=False, error="...")
3. **单服务器故障**: 不影响其他服务器，记录日志继续运行
4. **连接池耗尽**: 等待可用连接或超时返回错误
