# HMAC Generator

> HMAC Generator creates Hash-based Message Authentication Codes for data integrity and authentication. Supports SHA-256, SHA-384, SHA-512, SHA-1, and MD5 algorithms.

<img src="https://apiverve.web.app/publicapis/logos/hmac.svg" width="80" height="80" />

[![npm](https://img.shields.io/npm/v/@apiverve/hmac.svg?style=flat&logo=npm)](https://www.npmjs.com/package/@apiverve/hmac)
[![PyPI](https://img.shields.io/pypi/v/apiverve-hmac.svg?style=flat&logo=python)](https://pypi.org/project/apiverve-hmac/)
[![NuGet](https://img.shields.io/nuget/v/APIVerve.API.HMACGenerator.svg?style=flat&logo=nuget)](https://www.nuget.org/packages/APIVerve.API.HMACGenerator)
[![OpenAPI](https://img.shields.io/badge/OpenAPI-3.0-green.svg)](openapi.json)
[![Try it](https://img.shields.io/badge/Try%20it-Postman-orange.svg)](https://www.postman.com/apiverve/apiverve-workspace/collection/a6utjd8/apiverve-api-collection)

---

## 🚀 Quick Start

```bash
curl -X GET "https://api.apiverve.com/v1/hmac" \
  -H "x-api-key: YOUR_API_KEY"
```

[Get your API key →](https://dashboard.apiverve.com)

**[🧪 Live Test this API →](https://apiverve.com/marketplace/hmac)**

---

## 📖 Description

HMAC Generator creates Hash-based Message Authentication Codes for data integrity and authentication. Supports SHA-256, SHA-384, SHA-512, SHA-1, and MD5 algorithms.

---

## ✅ Example Response

```json
{
  "status": "ok",
  "data": { ... }
}
```

[See full examples →](examples/)

---

## 📄 Response Formats

This API supports multiple response formats:

| Format | Content-Type | How to Request |
|--------|--------------|----------------|
| JSON | `application/json` | Default, or `Accept: application/json` |
| XML | `application/xml` | `Accept: application/xml` or `?format=xml` |
| YAML | `application/yaml` | `Accept: application/yaml` or `?format=yaml` |

---

## 🔌 Advanced Access

This API is also available via:

- **[MCP Server](https://github.com/apiverve/mcp-server)** - Use with Claude Desktop, Cursor, and other MCP-compatible AI tools
- **[GraphQL](https://docs.apiverve.com/graphql)** - Query multiple APIs in a single request

---

## 🔗 Resources

- **[Live Test](https://apiverve.com/marketplace/hmac)** - Test in your browser
- **[Try in Postman](https://www.postman.com/apiverve/apiverve-workspace/collection/a6utjd8/apiverve-api-collection)** - Import to Postman
- **[OpenAPI Spec](openapi.json)** - Full API specification
- **[Documentation](https://docs.apiverve.com/ref/hmac)** - Full documentation
- **[Pricing](https://apiverve.com/pricing)** - View pricing plans

---

## 📊 API Information

- **Category:** Security
- **Average Latency:** 167ms
- **Health Status:** 99%

---

## 📦 Installation

### NPM
```bash
npm install @apiverve/hmac
```

### PyPI
```bash
pip install apiverve-hmac
```

### NuGet
```bash
dotnet add package APIVerve.API.HMACGenerator
```

---

## 🐛 Issues

Found a bug? [Report it here](../../issues)

---

[← Back to API Catalog](../../)
