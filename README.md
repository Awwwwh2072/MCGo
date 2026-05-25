# McGo - MQTT File Synchronization System

基于 MQTT 协议的文件同步系统。服务端扫描目录生成 JSON 文件树，客户端拉取对比后自动下载缺失或变更的文件。支持 RSA 认证、AES-256-GCM 加密、压缩传输。
对于可以通过M站或者F站等源下载的，推荐不直接从服务器拉取资源。

## 环境要求

- Python >= 3.10
- MQTT Broker (Mosquitto 或其他)

## 安装

```bash
pip install -e .
```

依赖: `paho-mqtt`, `cryptography`, `watchdog`

## 快速开始

### 1. 准备 MQTT Broker

确保 MQTT Broker 已启动（默认连接 `localhost:1883`）。例如：

```bash
# Windows (Mosquitto)
mosquitto -v
```

### 2. 服务端

```bash
# 生成默认配置文件
python -m mcgo server --init

# 生成 AES-256 加密密钥，将输出填入配置文件
python -m mcgo server --gen-encryption-key

# 生成 RSA 密钥对
python -m mcgo server --gen-keys

# 编辑配置文件 mcgo_server.toml
#  - 填入 encryption_key
#  - 设置 scan_directory 指向要同步的目录

# 启动服务端
python -m mcgo server --config ./mcgo_server.toml
```

### 3. 客户端

```bash
# 生成默认配置和密钥
python -m mcgo client --init
python -m mcgo client --gen-keys

# 编辑配置文件 mcgo_client.toml
#  - 填入与服务端相同的 encryption_key
#  - 设置 sync_directory 指向本地同步目录

# 将客户端公钥 (keys/client_public.pem) 注册到服务端 clients.toml:
#   [client.你的客户端ID]
#   public_key_path = "keys/client_public.pem"

# 单次同步
python -m mcgo client --sync

# 持续监控模式
python -m mcgo client --watch
```

## 工作原理

### 认证流程

```
Client                              Server
  |                                    |
  |--- hello (client_id) ------------->|
  |<-- challenge (32 bytes random) ----|
  |--- signed challenge -------------->|
  |<-- auth_result (success/fail) -----|
```

- RSA-2048 挑战-应答认证
- 服务端通过 `clients.toml` 注册合法客户端公钥

### 文件同步流程

1. 服务端扫描目录，为每个文件计算 SHA-256 哈希，生成 JSON 文件树
2. 文件树通过 MQTT Retained 消息发布，新客户端连接即获取
3. 客户端对比本地与远程文件树，找出缺失/变更的文件
4. 客户端请求文件 → 服务端压缩(如需要)+加密 → 分块传输 → 客户端解密解压写入

### 路径映射（`clientmods` → `mods`）

服务端同步目录下的 `clientmods/` 在客户端对应为 `sync_directory/mods/`：比对哈希时使用该映射，下载时仍向服务端请求 `clientmods/...` 路径，文件写入本地 `mods/...`。其他路径仍为一对一同步。

### 内置忽略规则（`mods` 目录）

除 `.mcgoignore` 外，程序按角色附带一层默认规则（仍可用 `.mcgoignore` 中的模式覆盖，例如 `!mods/server-keep.jar` 取反）。**仅当路径中某一目录段名为 `mods`（精确匹配，不是 `clientmods` 等子串）且当前节点为文件时生效。**

| 角色 | 默认忽略 |
|------|----------|
| 服务端 | `mods/` 目录树内，文件名为 `server-` 前缀的文件（不扫描、不发布到文件树；`clientmods/` 不受影响） |
| 客户端 | `mods/` 目录树内，文件名为 `client-` 前缀的文件（本地扫描与与远程对比时均跳过，避免被判定为缺失而下载） |

示例：`pack/mods/foo/server-bar.jar` 会被服务端忽略；`clientmods/server-bar.jar` 仍由服务端发布。Python 客户端与 C# `McGo.Client` 行为一致。

## C# 嵌入客户端（.NET 8）

仓库内提供原生类库 [`csharp/McGo.Client/McGo.Client.csproj`](csharp/McGo.Client/McGo.Client.csproj)，协议与 Python 客户端一致，适合在 C# 宿主中直接引用（无需 Python 运行时）。

### 构建

```bash
dotnet build csharp/McGo.Client/McGo.Client.csproj -c Release
```

### 宿主引用

在宿主 `.csproj` 中添加：

```xml
<ItemGroup>
  <ProjectReference Include="path/to/McGo/csharp/McGo.Client/McGo.Client.csproj" />
</ItemGroup>
```

### 调用示例

```csharp
using McGo.Client;

await using var client = new McGoClient(@"C:\path\to\mcgo_client.toml");
var result = await client.SyncAsync(cancellationToken: ct, timeout: TimeSpan.FromSeconds(60));
// result.Success, result.FilesDownloaded, result.FilesFailed, result.Errors
```

配置文件仍使用根目录的 `mcgo_client.toml`（与 Python 客户端相同）。可选注入 `Microsoft.Extensions.Logging.ILogger` 构造函数第二参数。

### 与 Python 客户端的关系

- 推荐使用本机 **McGo.Client** 做嵌入；若已有 Python.NET 环境，也可继续用下文「C# 互操作」方式调用 Python 模块。

### 加密方案

- **对称加密**: AES-256-GCM，每块独立 nonce，AAD 绑定传输上下文
- **压缩**: zlib，`.zip`/`.jar`/`.png` 等已压缩格式自动跳过
- **认证**: RSA-2048 + SHA-256

## 配置说明

### mcgo_server.toml

| 字段 | 说明 |
|---|---|
| `server.mqtt_host` | MQTT Broker 地址 |
| `server.mqtt_port` | MQTT Broker 端口 |
| `server.scan_directory` | 要扫描同步的目录 |
| `server.ignore_file` | 忽略规则文件 (默认 .mcgoignore) |
| `server.encryption_key` | AES-256 密钥 (base64, 32 bytes) |
| `auth.server_private_key` | 服务端 RSA 私钥路径 |
| `auth.clients_file` | 客户端公钥注册表路径 |
| `auth.challenge_timeout_seconds` | 认证挑战超时时间 |

### mcgo_client.toml

| 字段 | 说明 |
|---|---|
| `client.client_id` | 客户端唯一标识 |
| `client.sync_directory` | 本地同步目录 |
| `client.encryption_key` | 加密密钥 (必须与服务端一致) |
| `auth.client_private_key` | 客户端 RSA 私钥路径 |

## .mcgoignore 规则

与上文「内置忽略规则」叠加：先应用角色默认规则，再按文件中自上而下逐条匹配（含 `!` 取反）。兼容 gitignore 语法子集：

```gitignore
# 注释
*.tmp           # 匹配所有 .tmp 文件
__pycache__/    # 匹配目录
!important.tmp  # 取反
logs/**/debug.log  # ** 匹配任意层级
```

## MQTT Topic 协议

所有 topic 位于 `mcgo/v1/` 命名空间：

| Topic | 方向 | 用途 |
|---|---|---|
| `server/announce` | S→C | 服务端在线通知 (Retained) |
| `server/tree` | S→C | 文件树 JSON (Retained) |
| `server/challenge/{id}` | S→C | 认证挑战 |
| `server/auth_result/{id}` | S→C | 认证结果 |
| `server/file/{fid}/meta` | S→C | 文件传输元信息 |
| `server/file/{fid}/chunk/{seq}` | S→C | 加密分块数据 |
| `server/file/{fid}/done` | S→C | 传输完成信号 |
| `client/{id}/hello` | C→S | 发起认证 |
| `client/{id}/auth_response` | C→S | 签名回复 |
| `client/{id}/file_request` | C→S | 文件请求 |
| `client/{id}/status` | C→S | 客户端心跳 |

## C# 互操作（Python.NET）

若希望在 .NET 进程中直接调用 Python 实现的 `McGoClient`（需安装 Python 与 `mcgo` 包），可使用 Python.NET：

```csharp
using Python.Runtime;
dynamic mcgo = Py.Import("mcgo.client");
dynamic client = mcgo.McGoClient("mcgo_client.toml");
dynamic result = client.sync();
// result = { "success": true, "files_downloaded": [...], ... }
```
