from typing import Any
from unittest.mock import Mock, patch

import pytest

from liminal.entity_schemas.api import list_entity_schemas
from liminal.entity_schemas.compare import compare_entity_schemas
from liminal.entity_schemas.entity_schema_models import (
    EntitySchemaInputModel,
    EntitySchemaModel,
)
from liminal.entity_schemas.operations import UpdateEntitySchema
from liminal.entity_schemas.utils import convert_entity_schema_to_internal_schema
from liminal.enums import (
    BenchlingEntitySchemaEndpointType,
    BenchlingEntityType,
    BenchlingFieldType,
    BenchlingNamingStrategy,
)
from liminal.orm.base_model import BaseModel
from liminal.orm.column import Column
from liminal.orm.mixins import ContainerMixin
from liminal.orm.name_template import NameTemplate
from liminal.orm.schema_properties import SchemaProperties


def container_schema_data(**overrides: Any) -> dict[str, Any]:
    """A container schema as listed by GET /api/v3/container-schema/items."""
    return {
        "name": "Cell Container",
        "id": "consch_1",
        "description": "",
        "archived": False,
        "archiveReason": None,
        "fieldDefinitions": "https://example.benchling.com/api/v3/container-schema/consch_1/field-definitions/items",
        "createdAt": "2025-09-19T18:04:32.099528Z",
        "modifiedAt": "2026-07-09T21:10:51.472332Z",
        "systemName": "cell_container",
        "prefix": "CL-container",
        "registry": {"id": "src_1", "__typename": "Registry"},
        "type": "container",
        "__typename": "ContainerSchema",
        **overrides,
    }


def text_field_data() -> dict[str, Any]:
    return {
        "name": "Registry ID",
        "id": "tsf_1",
        "archived": False,
        "archiveReason": None,
        "createdAt": "2026-07-09T21:10:39.365109Z",
        "modifiedAt": "2026-07-09T21:10:51.472332Z",
        "systemName": "registry_id",
        "isRequired": False,
        "derivationType": "DIRECT",
        "description": None,
        "__typename": "TextFieldDefinition",
    }


def listed_container_schema() -> dict[str, Any]:
    def list_items(_service: Any, url: str, _include_archived: bool) -> list[dict]:
        if url == f"/api/v3/{BenchlingEntitySchemaEndpointType.CONTAINER.value}/items":
            return [container_schema_data()]
        return []

    with patch("liminal.entity_schemas.api.list_all_items_v3", side_effect=list_items):
        (schema,) = list_entity_schemas(Mock())
    return schema


class TestContainerSchemas:
    def test_list_reports_prefix_as_item_id_prefix(self) -> None:
        schema = listed_container_schema()
        assert schema["itemIdPrefix"] == "CL-container"
        assert "prefix" not in schema

    def test_converts_to_container_schema_properties(self) -> None:
        schema = EntitySchemaModel.model_validate(
            {**listed_container_schema(), "fields": [text_field_data()]}
        )
        props, name_template, fields = convert_entity_schema_to_internal_schema(
            schema, {}, {}, {}
        )
        assert props == SchemaProperties(
            name="Cell Container",
            warehouse_name="cell_container",
            prefix="CL-container",
            entity_type=BenchlingEntityType.CONTAINER,
        )
        assert name_template == NameTemplate()
        assert fields["registry_id"].type == BenchlingFieldType.TEXT

    def test_registry_properties_cannot_be_set(self) -> None:
        with pytest.raises(ValueError, match="naming_strategies"):
            SchemaProperties(
                name="Cell Container",
                warehouse_name="cell_container",
                prefix="CL-container",
                entity_type=BenchlingEntityType.CONTAINER,
                naming_strategies={BenchlingNamingStrategy.NEW_IDS},
            )

    def test_create_body_uses_prefix_and_no_registry_properties(self) -> None:
        service = Mock(organization_id="ent_org")
        props = SchemaProperties(
            name="Cell Container",
            warehouse_name="cell_container",
            prefix="CL-container",
            entity_type=BenchlingEntityType.CONTAINER,
        )
        body = EntitySchemaInputModel.from_benchling_props(
            props, [], service
        ).model_dump(exclude_none=True)
        assert body == {
            "name": "Cell Container",
            "systemName": "cell_container",
            "prefix": "CL-container",
            "organizationId": "ent_org",
            "description": "",
            "fieldDefinitions": [],
        }

    def test_update_body_uses_prefix(self) -> None:
        schema = EntitySchemaModel.model_validate(listed_container_schema())
        body = schema.to_entity_schema_input_update_model(
            {"prefix": "CELL", "name": "Cell Tube"}
        ).model_dump(exclude_unset=True)
        assert body == {"prefix": "CELL", "name": "Cell Tube"}

    def test_compare_updates_container_schema_properties(self) -> None:
        class MockCellContainer(BaseModel, ContainerMixin):
            __schema_properties__ = SchemaProperties(
                name="Mock Cell Container",
                warehouse_name="mock_cell_container",
                prefix="CELL",
                entity_type=BenchlingEntityType.CONTAINER,
            )
            registry_id = Column(
                name="Registry ID", type=BenchlingFieldType.TEXT, required=False
            )

        benchling_props = SchemaProperties(
            name="Mock Cell Container",
            warehouse_name="mock_cell_container",
            prefix="CL-container",
            entity_type=BenchlingEntityType.CONTAINER,
        )
        registry_id = MockCellContainer.registry_id.properties
        with (
            patch(
                "liminal.entity_schemas.compare.get_converted_entity_schemas",
                return_value=[
                    (benchling_props, NameTemplate(), {"registry_id": registry_id})
                ],
            ),
            patch(
                "liminal.entity_schemas.compare.BaseModel.get_all_subclasses",
                return_value=[MockCellContainer],
            ),
        ):
            ops = compare_entity_schemas(Mock(), {"mock_cell_container"})
        (compare_op,) = ops["mock_cell_container"]
        assert isinstance(compare_op.op, UpdateEntitySchema)
        assert compare_op.op.update_props.model_dump(exclude_unset=True) == {
            "prefix": "CELL"
        }
