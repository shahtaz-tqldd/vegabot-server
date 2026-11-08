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

Provide the chatbot slug as `chatbot`. Returns the chatbot details in `data`.

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
- Read-only response fields: `id`, `slug`, `created_at`, `updated_at`.
- Successful responses use `{ "status": 200, "success": true, "message": "...", "data": ... }`. List responses also include `meta`.
