---
name: picture
description: Use this skill when the user wants to generate images, edit existing images, restyle pictures, remove or replace backgrounds, create posters, illustrations, product shots, social media visuals, or other AI-generated images through the WhatAI-compatible OpenAI API at https://api.whatai.cc. It supports listing models via /v1/models and creating or editing images through /v1/chat/completions with the gemini-3.1-flash-image-preview model.
compatibility: Requires Python 3 and network access to api.whatai.cc
---

# Picture Skill

This skill handles both image generation and image editing through the WhatAI API.

Use the bundled script at [scripts/whatai_image_client.py](scripts/whatai_image_client.py) for all API calls. The script is designed for agent use and prints structured JSON so the result can be returned directly to the user.

## Default Service Settings

- Base URL: `https://api.whatai.cc`
- Model discovery: `GET /v1/models`
- Image generation/editing: `POST /v1/chat/completions`
- Default image model: `gemini-3.1-flash-image-preview`

The script supports `WHATAI_API_KEY` and `WHATAI_BASE_URL` environment variables.
You must provide a valid WhatAI key through `WHATAI_API_KEY` or `--api-key`.

## Workflow

### 1. Pick the right action

- For pure image generation from text, use `generate`.
- For editing or restyling an existing image, use `edit`.
- When the user asks what models are available, use `list-models`.

### 2. Prefer explicit prompts

When generating or editing, prompts work best when they include:

- Subject
- Style
- Composition
- Lighting or mood
- Output intent, such as poster, avatar, product shot, thumbnail, or illustration

For editing requests, preserve the parts the user wants to keep and state only the changes that should happen.

### 3. Return the result clearly

The API currently returns image results inside `choices[0].message.content` as Markdown image links, for example:

```text
![image](https://...)
```

The script extracts these URLs automatically and prints:

- `image_urls`
- `primary_image_url`
- `saved_files`
- `primary_saved_file`
- `raw_content`
- `usage`
- `request_payload`

Return the first usable image URL to the user, and include the prompt you used when it helps with reproducibility.
If the user wants a local artifact, use `--output-file` or `--download-dir`.

## Commands

Set your API key first:

```bash
export WHATAI_API_KEY="your-whatai-api-key"
```

### List available image-related models

```bash
python3 scripts/whatai_image_client.py list-models
```

To list all models without filtering:

```bash
python3 scripts/whatai_image_client.py list-models --all
```

### Generate an image

```bash
python3 scripts/whatai_image_client.py generate \
  --prompt "A cinematic product photo of a glass perfume bottle on wet black stone, dramatic rim lighting, luxury ad style"
```

### Generate and save the main result locally

```bash
python3 scripts/whatai_image_client.py generate \
  --prompt "A warm children's book illustration of a small fox reading under a tree" \
  --output-file "/absolute/path/to/output/fox.png"
```

### Edit an image from a URL

```bash
python3 scripts/whatai_image_client.py edit \
  --prompt "Keep the person unchanged, replace the background with a clean modern office, soft daylight, realistic style" \
  --image "https://example.com/source.jpg"
```

### Edit an image from a local file

```bash
python3 scripts/whatai_image_client.py edit \
  --prompt "Turn this sketch into a polished anime illustration with vivid colors" \
  --image "/absolute/path/to/input.png"
```

### Edit and save all returned images into a folder

```bash
python3 scripts/whatai_image_client.py edit \
  --prompt "Keep the product shape unchanged, replace the background with a clean beige studio setup" \
  --image "/absolute/path/to/product.jpg" \
  --download-dir "/absolute/path/to/output-dir"
```

## Agent Guidance

### Generation tasks

When the user asks for a new image:

- Use `generate`
- Write a complete prompt from the user's request
- If the user gave a loose idea, fill in missing style/composition details conservatively

### Editing tasks

When the user asks to modify an existing image:

- Use `edit`
- Pass every provided source image with repeated `--image`
- Keep the prompt focused on changes, plus what must remain unchanged

### Model choice

Default to `gemini-3.1-flash-image-preview` unless the user explicitly requests another available image model.

### Output handling

After running the script:

- Use `primary_image_url` as the main result
- If `primary_saved_file` exists, prefer returning that local path as the final artifact
- If no URL is extracted, inspect `raw_content`
- If the API returns an error object, surface the error message plainly

## Notes

- Local image files are converted into `data:` URLs automatically for edit requests.
- Large local images may cost more tokens and can fail when account quota is low.
- If the current key returns `insufficient_user_quota`, set a fresh `WHATAI_API_KEY` before calling the script again.
- If you need deeper API details or payload examples, read [references/api.md](references/api.md).
