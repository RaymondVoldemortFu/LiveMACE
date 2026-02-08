# WHOIS Lookup

> Whois Lookup is a simple tool for checking the registration of a domain name. It returns the name and contact information of the domain owner, the domain registrar, and more.

<img src="https://apiverve.web.app/publicapis/logos/whoislookup.svg" width="80" height="80" />

[![npm](https://img.shields.io/npm/v/@apiverve/whoislookup.svg?style=flat&logo=npm)](https://www.npmjs.com/package/@apiverve/whoislookup)
[![PyPI](https://img.shields.io/pypi/v/apiverve-whoislookup.svg?style=flat&logo=python)](https://pypi.org/project/apiverve-whoislookup/)
[![NuGet](https://img.shields.io/nuget/v/APIVerve.API.WHOISLookup.svg?style=flat&logo=nuget)](https://www.nuget.org/packages/APIVerve.API.WHOISLookup)
[![OpenAPI](https://img.shields.io/badge/OpenAPI-3.0-green.svg)](openapi.json)
[![Try it](https://img.shields.io/badge/Try%20it-Postman-orange.svg)](https://www.postman.com/apiverve/apiverve-workspace/collection/a6utjd8/apiverve-api-collection)

---

## 🚀 Quick Start

```bash
curl -X GET "https://api.apiverve.com/v1/whoislookup" \
  -H "x-api-key: YOUR_API_KEY"
```

[Get your API key →](https://dashboard.apiverve.com)

**[🧪 Live Test this API →](https://apiverve.com/marketplace/whoislookup)**

---

## 📖 Description

Whois Lookup is a simple tool for checking the registration of a domain name. It returns the name and contact information of the domain owner, the domain registrar, and more.

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

- **[Live Test](https://apiverve.com/marketplace/whoislookup)** - Test in your browser
- **[Try in Postman](https://www.postman.com/apiverve/apiverve-workspace/collection/a6utjd8/apiverve-api-collection)** - Import to Postman
- **[OpenAPI Spec](openapi.json)** - Full API specification
- **[Documentation](https://docs.apiverve.com/ref/whoislookup)** - Full documentation
- **[Pricing](https://apiverve.com/pricing)** - View pricing plans

---

## 📊 API Information

- **Category:** Domain Data
- **Average Latency:** 1706ms
- **Health Status:** 99%

---

## 📦 Installation

### NPM
```bash
npm install @apiverve/whoislookup
```

### PyPI
```bash
pip install apiverve-whoislookup
```

### NuGet
```bash
dotnet add package APIVerve.API.WHOISLookup
```

---

## 🐛 Issues

Found a bug? [Report it here](../../issues)

---

[← Back to API Catalog](../../)
