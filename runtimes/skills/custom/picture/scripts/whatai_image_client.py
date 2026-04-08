#!/usr/bin/env python3

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
from pathlib import Path
from urllib import error, request


DEFAULT_BASE_URL = "https://api.whatai.cc"
DEFAULT_MODEL = "gemini-3.1-flash-image-preview"
IMAGE_MARKDOWN_PATTERN = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)\)")
URL_EXTENSION_PATTERN = re.compile(r"\.([a-zA-Z0-9]{2,6})(?:$|[?#])")


def build_headers(api_key):
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def get_api_key(explicit_key=None):
    api_key = (explicit_key or os.environ.get("WHATAI_API_KEY") or "").strip()
    if api_key:
        return api_key
    raise RuntimeError("Missing WhatAI API key. Set WHATAI_API_KEY or pass --api-key.")


def get_base_url(explicit_base_url=None):
    return (explicit_base_url or os.environ.get("WHATAI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def http_json(method, url, headers, payload=None):
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = request.Request(url=url, data=data, headers=headers, method=method)

    try:
        with request.urlopen(req) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {
                "error": {
                    "message": body or str(exc),
                    "type": "http_error",
                    "code": exc.code,
                }
            }
        raise RuntimeError(json.dumps(payload, ensure_ascii=False))
    except error.URLError as exc:
        raise RuntimeError(
            json.dumps(
                {
                    "error": {
                        "message": str(exc),
                        "type": "network_error",
                        "code": None,
                    }
                },
                ensure_ascii=False,
            )
        )


def guess_media_type(path):
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def path_to_data_url(path_str):
    path = Path(path_str).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {path}")
    media_type = guess_media_type(path)
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def normalize_image_input(value):
    if value.startswith("http://") or value.startswith("https://") or value.startswith("data:"):
        return value
    return path_to_data_url(value)


def extract_image_urls_from_text(text):
    if not isinstance(text, str):
        return []
    return IMAGE_MARKDOWN_PATTERN.findall(text)


def deep_collect_image_urls(node, found=None):
    if found is None:
        found = []

    if isinstance(node, dict):
        for key, value in node.items():
            if key in {"url", "image_url"} and isinstance(value, str) and value.startswith(("http://", "https://")):
                found.append(value)
            else:
                deep_collect_image_urls(value, found)
    elif isinstance(node, list):
        for item in node:
            deep_collect_image_urls(item, found)
    elif isinstance(node, str):
        found.extend(extract_image_urls_from_text(node))

    return found


def dedupe_keep_order(items):
    seen = set()
    ordered = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def detect_extension_from_response(resp, url):
    content_type = resp.headers.get_content_type()
    guessed = mimetypes.guess_extension(content_type or "")
    if guessed:
        return guessed

    match = URL_EXTENSION_PATTERN.search(url)
    if match:
        return f".{match.group(1).lower()}"

    return ".bin"


def download_binary(url):
    req = request.Request(url=url, method="GET")
    with request.urlopen(req) as resp:
        data = resp.read()
        extension = detect_extension_from_response(resp, url)
    return data, extension


def save_url_to_file(url, path):
    data, _ = download_binary(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path.resolve())


def save_url_to_directory(url, directory, stem, index):
    data, extension = download_binary(url)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}_{index}{extension}"
    path.write_bytes(data)
    return str(path.resolve())


def maybe_save_images(result, output_file=None, download_dir=None, file_stem="image"):
    image_urls = result.get("image_urls") or []
    saved_files = []

    if output_file and image_urls:
        primary_path = Path(output_file).expanduser().resolve()
        saved = save_url_to_file(image_urls[0], primary_path)
        saved_files.append(saved)
        result["primary_saved_file"] = saved
    else:
        result["primary_saved_file"] = None

    if download_dir and image_urls:
        directory = Path(download_dir).expanduser().resolve()
        for index, image_url in enumerate(image_urls, start=1):
            saved = save_url_to_directory(image_url, directory, file_stem, index)
            if saved not in saved_files:
                saved_files.append(saved)

    result["saved_files"] = saved_files
    return result


def build_user_content(prompt, image_inputs=None):
    content = [{"type": "text", "text": prompt}]
    for image in image_inputs or []:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": normalize_image_input(image)},
            }
        )
    return content


def build_messages(prompt, image_inputs=None, system_prompt=None):
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append(
        {
            "role": "user",
            "content": build_user_content(prompt, image_inputs=image_inputs),
        }
    )
    return messages


def create_completion(base_url, api_key, model, prompt, image_inputs=None, system_prompt=None):
    payload = {
        "model": model,
        "messages": build_messages(prompt, image_inputs=image_inputs, system_prompt=system_prompt),
    }

    response = http_json(
        "POST",
        f"{base_url}/v1/chat/completions",
        build_headers(api_key),
        payload=payload,
    )

    content = ""
    choices = response.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        content = message.get("content") or ""

    image_urls = dedupe_keep_order(
        extract_image_urls_from_text(content) + deep_collect_image_urls(response)
    )

    return {
        "ok": "error" not in response,
        "model": response.get("model", model),
        "primary_image_url": image_urls[0] if image_urls else None,
        "image_urls": image_urls,
        "raw_content": content,
        "usage": response.get("usage"),
        "request_payload": payload,
        "response": response,
    }


def list_models(base_url, api_key, include_all=False):
    response = http_json("GET", f"{base_url}/v1/models", build_headers(api_key))
    models = response.get("data") or []

    if not include_all:
        filtered = []
        for item in models:
            model_id = str(item.get("id", ""))
            endpoint_types = item.get("supported_endpoint_types") or []
            text = " ".join([model_id] + [str(x) for x in endpoint_types]).lower()
            if "image" in text:
                filtered.append(item)
        models = filtered

    return {
        "ok": True,
        "count": len(models),
        "models": models,
    }


def print_json(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def handle_list_models(args):
    result = list_models(
        base_url=get_base_url(args.base_url),
        api_key=get_api_key(args.api_key),
        include_all=args.all,
    )
    print_json(result)


def handle_generate(args):
    result = create_completion(
        base_url=get_base_url(args.base_url),
        api_key=get_api_key(args.api_key),
        model=args.model,
        prompt=args.prompt,
        system_prompt=args.system,
    )
    result = maybe_save_images(
        result,
        output_file=args.output_file,
        download_dir=args.download_dir,
        file_stem="generated_image",
    )
    print_json(result)


def handle_edit(args):
    result = create_completion(
        base_url=get_base_url(args.base_url),
        api_key=get_api_key(args.api_key),
        model=args.model,
        prompt=args.prompt,
        image_inputs=args.image,
        system_prompt=args.system,
    )
    result = maybe_save_images(
        result,
        output_file=args.output_file,
        download_dir=args.download_dir,
        file_stem="edited_image",
    )
    print_json(result)


def add_save_arguments(subparser):
    subparser.add_argument(
        "--output-file",
        help="Download the primary returned image to this exact local file path.",
    )
    subparser.add_argument(
        "--download-dir",
        help="Download all returned image URLs into this directory.",
    )


def build_parser():
    parser = argparse.ArgumentParser(
        description="Generate and edit images with the WhatAI-compatible OpenAI API."
    )
    parser.add_argument(
        "--api-key",
        help="Override WHATAI_API_KEY. Required unless WHATAI_API_KEY is already set.",
    )
    parser.add_argument("--base-url", help="Override WHATAI_BASE_URL.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    models_parser = subparsers.add_parser("list-models", help="List available models.")
    models_parser.add_argument(
        "--all",
        action="store_true",
        help="Return all models instead of only image-related models.",
    )
    models_parser.set_defaults(func=handle_list_models)

    generate_parser = subparsers.add_parser("generate", help="Generate an image from text.")
    generate_parser.add_argument("--prompt", required=True, help="Prompt for image generation.")
    generate_parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Model name. Defaults to {DEFAULT_MODEL}.",
    )
    generate_parser.add_argument(
        "--system",
        help="Optional system prompt for style or response behavior.",
    )
    add_save_arguments(generate_parser)
    generate_parser.set_defaults(func=handle_generate)

    edit_parser = subparsers.add_parser("edit", help="Edit an image using prompt + image input.")
    edit_parser.add_argument("--prompt", required=True, help="Editing instruction.")
    edit_parser.add_argument(
        "--image",
        action="append",
        required=True,
        help="Image URL, data URL, or local file path. Repeat for multiple reference images.",
    )
    edit_parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Model name. Defaults to {DEFAULT_MODEL}.",
    )
    edit_parser.add_argument(
        "--system",
        help="Optional system prompt for style or response behavior.",
    )
    add_save_arguments(edit_parser)
    edit_parser.set_defaults(func=handle_edit)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    try:
        args.func(args)
    except Exception as exc:
        try:
            payload = json.loads(str(exc))
        except json.JSONDecodeError:
            payload = {
                "ok": False,
                "error": {
                    "message": str(exc),
                    "type": exc.__class__.__name__,
                    "code": None,
                },
            }
        else:
            payload["ok"] = False

        print_json(payload)
        sys.exit(1)


if __name__ == "__main__":
    main()
