# McGo - MQTT File Synchronization System

基于 MQTT 协议的文件同步系统。服务端扫描目录生成 JSON 文件树，客户端拉取对比后自动下载缺失或变更的文件。支持 RSA 认证、AES-256-GCM 加密、压缩传输。

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

兼容 gitignore 语法子集：

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

## C# 互操作

`McGoClient` 类可独立实例化，供 Python.NET 调用：

```csharp
using Python.Runtime;
dynamic mcgo = Py.Import("mcgo.client");
dynamic client = mcgo.McGoClient("mcgo_client.toml");
dynamic result = client.sync();
// result = { "success": true, "files_downloaded": [...], ... }
```
