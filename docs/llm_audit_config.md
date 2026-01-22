# LLM 审计配置说明

## 环境变量配置

LLM 审计器使用**独立的**环境变量配置，与主 Agent 的配置分离。这样可以：
- 使用不同的模型进行审计（例如更便宜的模型）
- 使用不同的 API endpoint
- 独立管理审计器的 API 密钥

### 配置项

在 `.env` 文件中设置以下变量：

```env
# LLM 审计器配置（独立配置）
AUDIT_API_KEY="your-audit-api-key"           # 审计器的 API 密钥
AUDIT_BASE_URL="https://api.example.com/v1"  # 审计器的 API endpoint（可选）
AUDIT_MODEL="gpt-4o-mini"                    # 审计器使用的模型
```

### 默认值

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `AUDIT_API_KEY` | 使用 Agent 的 API key | 如果未设置，回退到主 Agent 配置 |
| `AUDIT_BASE_URL` | `None` | 未设置时使用 OpenAI 官方 endpoint |
| `AUDIT_MODEL` | `gpt-4o-mini` | 推荐使用更经济的模型 |

## 配置示例

### 示例 1: 使用 OpenAI 官方 API

```env
AUDIT_API_KEY="sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
AUDIT_MODEL="gpt-4o-mini"
# AUDIT_BASE_URL 留空即可
```

### 示例 2: 使用第三方中转 API

```env
AUDIT_API_KEY="sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
AUDIT_BASE_URL="https://api.bltcy.ai/v1"
AUDIT_MODEL="gpt-4.1"
```

### 示例 3: 使用本地 vLLM 部署

```env
AUDIT_API_KEY="dummy-key"  # 本地部署通常不需要真实 key
AUDIT_BASE_URL="http://localhost:8000/v1"
AUDIT_MODEL="Qwen/Qwen2.5-72B-Instruct"
```

## 生产环境配置

### Agent 配置（数据库）

主 Agent 的配置存储在数据库的 `accounts` 表中：
- `model`: 主 Agent 使用的模型（例如 "gpt-4"）
- `api_key`: 主 Agent 的 API 密钥
- `base_url`: 主 Agent 的 API endpoint

这些配置**不影响**审计器的配置。

### 审计器配置（环境变量）

审计器始终从环境变量读取配置：
- 在生产服务器的 `.env` 文件中设置 `AUDIT_*` 变量
- 或通过系统环境变量设置（Docker、K8s 等）

## 测试环境配置

测试脚本 `test_llm_auditor.py` 也使用相同的环境变量：

```bash
# 在 .env 文件中配置
AUDIT_API_KEY="your-key"
AUDIT_BASE_URL="https://api.example.com/v1"
AUDIT_MODEL="gpt-4o-mini"

# 运行测试
conda activate uvbench
python test_llm_auditor.py
```

## 配置验证

启用 LLM 审计后，日志会显示使用的配置：

```
INFO - Rule-Aware Agent initialized with rules: ...
INFO - LLM-based audit scoring enabled - Model: gpt-4o-mini
```

如果看到这行日志，说明审计器已正确初始化并使用了环境变量中的模型配置。

## 故障排除

### 问题：审计器未使用环境变量配置

**检查**：
1. 确认 `.env` 文件位于项目根目录
2. 确认环境变量名称正确（`AUDIT_API_KEY`，不是 `OPENAI_API_KEY`）
3. 检查日志中显示的模型名称是否正确

### 问题：审计器使用了 Agent 的配置

**原因**：
- `AUDIT_API_KEY` 未设置，系统回退到 Agent 配置
- 这是正常行为，作为后备方案

**解决**：
- 在 `.env` 文件中显式设置 `AUDIT_API_KEY`

### 问题：找不到 dotenv 模块

**解决**：
```bash
pip install python-dotenv
```

## 成本优化建议

1. **使用更便宜的模型**: 审计任务可以使用 `gpt-4o-mini` 或 `gpt-3.5-turbo`
2. **使用中转 API**: 第三方中转通常比官方 API 便宜
3. **按需启用**: 仅在需要评估时设置 `enable_llm_audit=True`

## 配置优先级

```
环境变量 AUDIT_* 
    ↓ 未设置
Agent 配置（数据库）
    ↓ 未设置  
默认值（gpt-4o-mini）
```

这样确保了：
- 测试环境可以使用独立配置
- 生产环境可以使用不同的审计模型
- 始终有后备方案，不会因配置缺失而失败
