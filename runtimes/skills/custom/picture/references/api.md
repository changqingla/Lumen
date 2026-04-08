# WhatAI Image API Notes

These notes document the behavior that has been validated for this skill.

## Endpoints

- Base URL: `https://api.whatai.cc`
- List models: `GET /v1/models`
- Image generate/edit: `POST /v1/chat/completions`

## Default model

- `gemini-3.1-flash-image-preview`

This model is present in `/v1/models` and supports image generation. It also accepts image input through OpenAI-compatible `image_url` message content for editing-style requests.

## Validated request shape

### Generate

```json
{
  "model": "gemini-3.1-flash-image-preview",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "Generate a simple flat illustration of a red apple on white background. Return the generated image."
        }
      ]
    }
  ]
}
```

### Edit

```json
{
  "model": "gemini-3.1-flash-image-preview",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "Edit this image: keep the apple, change the background to pastel blue, and make the style slightly more glossy. Return the edited image."
        },
        {
          "type": "image_url",
          "image_url": {
            "url": "https://example.com/source.jpg"
          }
        }
      ]
    }
  ]
}
```

## Validated response shape

The returned image URL currently appears inside:

- `choices[0].message.content`

In the validated responses, that field contained Markdown like:

```text
![image](https://files.closeai.fans/filesystem/output/....jpg)
```

The bundled script extracts the URL automatically with a Markdown image regex and also keeps the raw content.

## Local file output

The bundled CLI can optionally download returned images after generation or editing:

- `--output-file /path/to/file.png`: saves the first returned image to an exact file path
- `--download-dir /path/to/dir`: saves every returned image URL into a directory

When saving succeeds, the script adds:

- `primary_saved_file`
- `saved_files`

## Practical guidance

- Prefer remote image URLs when available.
- Local file paths are encoded as `data:` URLs by the script.
- If quota is low, requests with inline base64 images may fail earlier than simple text-only generation.
- If the API responds with `insufficient_user_quota`, replace the API key and retry.
- Keep prompts direct and explicit. Ask for the final image instead of conversational discussion.
