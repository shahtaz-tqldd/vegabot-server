# Chatbot API

All endpoints require authentication. Base path: `/api/v1/chatbots/`.

## Create

`POST /create/`

Provide JSON: `chatbot_name` (required), `business_type` (required); optional `description`, `tone`, `welcome_message`, `fallback_message`, `instructions`, `escalation_rule`, `never_answer`, `language`, `timezone`, `other_settings`, `status`, `logo`.

Returns the created chatbot in `data`.

## List

`GET /list/?page=<number>&page_size=<number>`

Returns chatbot summaries in `data` and pagination fields (`count`, `page`, `page_size`, `num_pages`, `next`, `previous`) in `meta`. `page_size` defaults to 20 (maximum 100).

## Details

`GET /details/?chatbot=<slug>`

Provide the chatbot slug as `chatbot`. Returns the chatbot details in `data`, including `speaking_pace` and `voice` (the voice it speaks with now, same shape as `selected` in [Chatbot voices](#chatbot-voices)).

## Update

`PUT /update/?chatbot=<slug>` (all required fields) or `PATCH /update/?chatbot=<slug>` (changed fields only).

Provide chatbot fields as JSON. `logo` may be replaced; send `clear_logo: true` to remove it. Do not send both. Returns the updated chatbot in `data`.

## Delete

`DELETE /delete/?chatbot=<slug>`

Provide the chatbot slug as `chatbot`. No request body. Returns a success response.

## Shared fields

- Required on create: `chatbot_name`, `business_type`.
- `tone`: `professional`, `friendly`, `casual`, `formal`, `witty`.
- `status`: `draft`, `active`, `disabled`.
- Features (booleans) choose what the agent can do:

  | Field | Default | Effect |
  | --- | --- | --- |
  | `knowledge_enabled` | `true` | Answer from documents, web pages, and raw text |
  | `products_enabled` | `true` | Search and recommend products |
  | `services_enabled` | `true` | Search and recommend services |
  | `product_ordering_enabled` | `false` | Products add-on: the agent can add products to a cart and take orders |

  At least one of knowledge, products, or services must be enabled, and `product_ordering_enabled` requires `products_enabled`; otherwise the request fails with `400`. A source is only used when it is enabled **and** has trained content. The list endpoint returns the same four fields.
- Read-only response fields: `id`, `slug`, `created_at`, `updated_at`.
- Successful responses use `{ "status": 200, "success": true, "message": "...", "data": ... }`. List responses also include `meta`.

- `speaking_pace`: `slow`, `normal` (default), `fast`. It can be updated through `/update/` or `/voices/select/`. The Live model has no speaking-rate setting, so pace is applied through the voice instructions and is approximate. A custom voice also keeps the pace of its recording.

## Voices

The voice is used in audio mode only, and each chatbot has its own. A chatbot speaks with its selected custom voice if it has one, otherwise with its prebuilt voice, otherwise with the platform default (`GEMINI_LIVE_CHAT_VOICE`).

### Voice options

`GET /voices/options/`

Everything a voice picker needs:

```json
{
  "prebuilt_voices": [
    { "type": "prebuilt", "name": "Kore", "gender": "female", "style": "Firm",
      "description": "...", "preview_url": "https://... or null" }
  ],
  "speaking_paces": [ { "value": "slow", "label": "Slow" }, ... ],
  "genders": [ { "value": "male", "label": "Male" }, ... ],
  "custom_voice": {
    "consent_statement": "I am the owner of this voice and I consent to ...",
    "sample_script": "Hi there, and thanks for stopping by. ...",
    "format": "WAV (PCM); converted to 24 kHz mono 16-bit",
    "sample_min_seconds": 10, "sample_max_seconds": 60, "sample_used_seconds": 20,
    "consent_max_seconds": 30, "max_upload_mb": 15, "max_per_chatbot": 5
  }
}
```

The list has 10 voices: female Kore, Aoede, Leda, Zephyr and Sulafat, and male Puck, Charon, Fenrir, Orus and Achird. `preview_url` is `null` until that voice's preview has been generated.

### Voice preview

`GET /voices/preview/?voice=<name>`

Returns `{ "voice": "Kore", "preview_url": "https://..." }`, a WAV of the voice saying a short greeting. The first request for a voice generates the clip with the Live model, which takes a few seconds, and stores it. Later requests return the stored clip. Returns `503` if the clip cannot be generated.

### Chatbot voices

`GET /voices/list/?chatbot=<slug>`

Available on your own chatbots and on demo chatbots:

```json
{
  "selected": { "type": "prebuilt", "name": "Puck", "gender": "male", "style": "Upbeat",
                "description": "...", "preview_url": null, "is_default": true },
  "speaking_pace": "normal",
  "custom_voices": [
    { "type": "custom", "id": "uuid", "name": "Founder", "gender": "male",
      "sample_duration_seconds": 42.5, "sample_url": "https://...signed",
      "is_selected": false, "created_at": "..." }
  ]
}
```

`sample_url` is a short-lived link for listening to the recorded sample. It is only included for the chatbot's owner. When a custom voice is selected, `selected` is that custom voice.

### Select voice

`PATCH /voices/select/?chatbot=<slug>` (owner only)

```json
{ "voice_type": "prebuilt", "voice_name": "Kore", "speaking_pace": "slow" }
{ "voice_type": "custom", "custom_voice": "<custom voice uuid>" }
```

`speaking_pace` is optional. Returns `{ "selected": {...}, "speaking_pace": "..." }`. The new voice applies from the next voice session.

### Add custom voice

`POST /voices/custom/create/?chatbot=<slug>` (owner only, multipart)

| Field | Required | Notes |
| --- | --- | --- |
| `name` | yes | Unique per chatbot, max 60 characters |
| `gender` | yes | `male` or `female` |
| `sample_audio` | yes | WAV, 10-60 s of the speaker talking naturally (for example reading `sample_script`) |
| `consent_audio` | yes | WAV of the same speaker reading `consent_statement` |
| `consent_confirmed` | yes | Must be `true`: the voice owner agrees to the voice being replicated |

Any PCM WAV is accepted (8/16/24/32-bit, mono or stereo, any sample rate) and stored as 24 kHz mono 16-bit, privately in R2. Record both clips in a quiet room with the same microphone. The Live model uses the first 20 seconds of the sample. Returns the custom voice with its `sample_url` (`201`). Errors (`400`) cover a sample of the wrong length, a silent or non-WAV file, a duplicate name, and the per-chatbot limit.

Browser recorders usually produce WebM/Opus. Record PCM with an AudioWorklet (as the voice mode already does) and wrap it in a WAV header.

### Delete custom voice

`DELETE /voices/custom/delete/?chatbot=<slug>&voice=<uuid>` (owner only)

Deletes the voice and both recordings. A chatbot that was using it falls back to its prebuilt voice.
