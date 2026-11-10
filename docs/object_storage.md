## Cloudflare R2 storage

The application uses the `vegabot` R2 bucket with this object layout:

```text
files/{chatbot_id}/{upload_id}/{filename}
images/users/{image}
images/workspaces/{image}
images/chatbots/{image}
images/config/{image}
```

Image URLs are returned as `R2_PUBLIC_URL/{object_key}`, for example
`https://assets.example.com/images/users/user-id.webp`. Set the remaining
`R2_*` variables documented in `.env.example`. Images use unique object names
and configurable `R2_IMAGE_CACHE_CONTROL` metadata, so the `images/*` path is
ready for long-lived Cloudflare caching.

The public domain must only serve `images/*`. Block `files/*` at Cloudflare (or
route the domain through a Worker that only permits `images/*`) and disable the
bucket's `r2.dev` public URL. Knowledge files under `files/*` are delivered using
short-lived presigned S3 API URLs.
