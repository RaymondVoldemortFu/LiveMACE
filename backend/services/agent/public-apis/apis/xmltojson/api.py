import xml.etree.ElementTree as ET
import json


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _elem_to_dict(elem):
    d = {}
    if elem.attrib:
        d["@attributes"] = elem.attrib
    if list(elem):
        for child in elem:
            cd = _elem_to_dict(child)
            if child.tag in d:
                if not isinstance(d[child.tag], list):
                    d[child.tag] = [d[child.tag]]
                d[child.tag].append(cd)
            else:
                d[child.tag] = cd
    if elem.text and elem.text.strip():
        d["#text"] = elem.text.strip()
    elif not d:
        return elem.text or ""
    return d


def run(params: dict) -> dict:
    params = params or {}
    xml_input = params.get("xml") or params.get("input") or params.get("data")
    if xml_input is None or not str(xml_input).strip():
        return _error("Missing required parameter: xml or input")
    raw = str(xml_input).strip()
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        return _error(f"Invalid XML: {e}")
    out = {root.tag: _elem_to_dict(root)}
    data = {"json": out, "xml_length": len(raw)}
    return {"status": "ok", "error": None, "data": data}
