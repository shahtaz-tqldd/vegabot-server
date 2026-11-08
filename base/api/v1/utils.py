CONFIG_SECTIONS = {
    "feature": ("maintenance_mode",),
    "models": ("text_llm_model", "live_llm_model"),
    "announcement": (
        "notify_banner_enabled",
        "notify_banner_text",
        "notify_banner_url",
    ),
    "audit": ("created_at", "updated_at", "updated_by"),
}


def serialize_config_sections(instance, sections, document_type=None):
    data = {}
    for section in sections:
        fields = CONFIG_SECTIONS[section]

        values = {}
        for field in fields:
            value = getattr(instance, field)
            if field == "updated_by":
                value = str(value.pk) if value else None
            elif field in ("created_at", "updated_at"):
                value = value.isoformat() if value else None
            values[field] = value
        data[section] = values
    return data
