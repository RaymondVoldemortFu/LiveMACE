# IP Lookup

> IP Lookup is a simple tool for looking up the location of an IP address. It returns the country, city, and more.

<img src="https://apiverve.web.app/publicapis/logos/iplookup.svg" width="80" height="80" />

[![npm](https://img.shields.io/npm/v/@apiverve/iplookup.svg?style=flat&logo=npm)](https://www.npmjs.com/package/@apiverve/iplookup)
[![PyPI](https://img.shields.io/pypi/v/apiverve-iplookup.svg?style=flat&logo=python)](https://pypi.org/project/apiverve-iplookup/)
[![NuGet](https://img.shields.io/nuget/v/APIVerve.API.IPLookup.svg?style=flat&logo=nuget)](https://www.nuget.org/packages/APIVerve.API.IPLookup)
[![OpenAPI](https://img.shields.io/badge/OpenAPI-3.0-green.svg)](openapi.json)
[![Try it](https://img.shields.io/badge/Try%20it-Postman-orange.svg)](https://www.postman.com/apiverve/apiverve-workspace/collection/a6utjd8/apiverve-api-collection)

---

## 🚀 Quick Start

```bash
curl -X GET "https://api.apiverve.com/v1/iplookup" \
  -H "x-api-key: YOUR_API_KEY"
```

[Get your API key →](https://dashboard.apiverve.com)

**[🧪 Live Test this API →](https://apiverve.com/marketplace/iplookup)**

---

## 📖 Description

IP Lookup is a simple tool for looking up the location of an IP address. It returns the country, city, and more.

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

- **[Live Test](https://apiverve.com/marketplace/iplookup)** - Test in your browser
- **[Try in Postman](https://www.postman.com/apiverve/apiverve-workspace/collection/a6utjd8/apiverve-api-collection)** - Import to Postman
- **[OpenAPI Spec](openapi.json)** - Full API specification
- **[Documentation](https://docs.apiverve.com/ref/iplookup)** - Full documentation
- **[Pricing](https://apiverve.com/pricing)** - View pricing plans

---

## 📊 API Information

- **Category:** Networking
- **Average Latency:** 1008ms
- **Health Status:** 99%

---

## 📦 Installation

### NPM
```bash
npm install @apiverve/iplookup
```

### PyPI
```bash
pip install apiverve-iplookup
```

### NuGet
```bash
dotnet add package APIVerve.API.IPLookup
```

---

## 🐛 Issues

Found a bug? [Report it here](../../issues)

---

[← Back to API Catalog](../../)
