from rest_framework import serializers

from base.models import VegabotConfig
from base.api.v1.utils import CONFIG_SECTIONS


class VegabotConfigUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = VegabotConfig
        fields = tuple(
            field
            for section, section_fields in CONFIG_SECTIONS.items()
            if section != "audit"
            for field in section_fields
        )

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            return super().to_internal_value(data)

        normalized_data = data.copy()
        for section, fields in CONFIG_SECTIONS.items():
            if section == "audit" or section not in data:
                continue

            section_data = data[section]
            if not isinstance(section_data, dict):
                raise serializers.ValidationError(
                    {section: "Expected an object containing configuration fields."}
                )

            for field in fields:
                if field in section_data:
                    normalized_data[field] = section_data[field]

        return super().to_internal_value(normalized_data)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Provide at least one configuration field to update.")
        return attrs

    def update(self, instance, validated_data):
        request = self.context.get("request")
        if request is not None:
            validated_data["updated_by"] = request.user

        return super().update(instance, validated_data)
