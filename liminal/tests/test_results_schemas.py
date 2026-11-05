from typing import Any
from unittest.mock import Mock, patch

import pytest

from liminal.base.properties.base_field_properties import BaseFieldProperties
from liminal.enums import BenchlingFieldType
from liminal.orm.base_results_model import BaseResultsModel
from liminal.orm.column import Column
from liminal.orm.results_schema_properties import ResultsSchemaProperties
from liminal.results_schemas.compare import compare_results_schemas
from liminal.results_schemas.models.results_schema_model import (
    ResultsSchemaFieldModel,
)
from liminal.results_schemas.operations import (
    ArchiveResultsSchemaField,
    CreateResultsSchema,
    CreateResultsSchemaField,
    ReorderResultsSchemaFields,
    UnarchiveResultsSchema,
    UpdateResultsSchema,
    UpdateResultsSchemaField,
)
from liminal.results_schemas.utils import (
    convert_result_schema_field_to_field_properties,
)

OPS = "liminal.results_schemas.operations"


def field_data(system_name: str, **overrides: Any) -> dict[str, Any]:
    """A result schema field as listed by the v3 field-definitions endpoint."""
    return {
        "id": f"fld_{system_name}",
        "name": system_name.title(),
        "systemName": system_name,
        "archived": False,
        "archiveReason": None,
        "createdAt": "2026-01-01T00:00:00Z",
        "modifiedAt": "2026-01-01T00:00:00Z",
        "isRequired": False,
        "derivationType": "DIRECT",
        "description": None,
        "__typename": "TextFieldDefinition",
        **overrides,
    }


SCHEMA = {
    "id": "assaysch_1",
    "name": "Assay",
    "systemName": "assay",
    "archived": False,
}


def patched_schema(fields: list[dict[str, Any]], schema: dict[str, Any] = SCHEMA):  # type: ignore[no-untyped-def]
    return (
        patch(f"{OPS}.list_results_schemas", return_value=[schema]),
        patch(f"{OPS}.list_result_schema_field_definitions", return_value=fields),
        patch(f"{OPS}.set_results_schema_field_definitions"),
    )


def submitted_fields(fields: list[dict[str, Any]], op: Any) -> list[dict[str, Any]]:
    schemas, listed, submit = patched_schema(fields)
    with schemas, listed, submit as set_fields:
        op.execute(Mock())
    return set_fields.call_args.args[2]


class TestResultsSchemaConversion:
    def test_snapshot_fields_are_marked(self) -> None:
        field = ResultsSchemaFieldModel.model_validate(
            field_data(
                "master_mrna",
                derivationType="SNAPSHOT",
                __typename="AnyEntityLinkFieldDefinition",
                isMulti=False,
            )
        )
        props = convert_result_schema_field_to_field_properties(field, {}, {}, {})
        assert props.is_snapshot is True
        assert "parent_link" not in props.model_dump(exclude_unset=True)

    def test_direct_fields_match_their_column(self) -> None:
        field = ResultsSchemaFieldModel.model_validate(field_data("notes"))
        props = convert_result_schema_field_to_field_properties(field, {}, {}, {})
        assert (
            props
            == Column(
                name="Notes", type=BenchlingFieldType.TEXT, required=False
            ).properties
        )


class TestResultsSchemaOperations:
    def test_create_sends_system_name_organization_and_decimal_units(self) -> None:
        service = Mock(organization_id="ent_org")
        op = CreateResultsSchema(
            ResultsSchemaProperties(name="Assay", warehouse_name="assay"),
            [
                BaseFieldProperties(
                    name="Concentration",
                    warehouse_name="concentration",
                    type=BenchlingFieldType.DECIMAL,
                    required=False,
                    unit_name="Nanogram per uL",
                )
            ],
        )
        with (
            patch(f"{OPS}.list_results_schemas", return_value=[]),
            patch(
                "liminal.entity_schemas.entity_schema_models.get_unit_id_from_name",
                return_value="unit_ng",
            ),
            patch(f"{OPS}.create_results_schema") as create,
        ):
            op.execute(service)
        assert create.call_args.args[1] == {
            "organizationId": "ent_org",
            "name": "Assay",
            "systemName": "assay",
            "fieldDefinitions": [
                {
                    "name": "Concentration",
                    "systemName": "concentration",
                    "type": "DECIMAL",
                    "isRequired": False,
                    "unitId": "unit_ng",
                }
            ],
        }

    def test_create_refuses_snapshot_fields(self) -> None:
        op = CreateResultsSchema(
            ResultsSchemaProperties(name="Assay", warehouse_name="assay"),
            [
                BaseFieldProperties(
                    name="Master",
                    warehouse_name="master",
                    type=BenchlingFieldType.ENTITY_LINK,
                    required=False,
                    is_snapshot=True,
                )
            ],
        )
        with patch(f"{OPS}.list_results_schemas", return_value=[]):
            with pytest.raises(ValueError, match="snapshot fields"):
                op.validate(Mock())

    def test_create_field_resubmits_every_field_and_keeps_legal_text(self) -> None:
        fields = [
            field_data("notes"),
            field_data("old", archived=True, archiveReason="Made in error"),
            field_data(
                "amount",
                __typename="DecimalFieldDefinition",
                legalTextDropdown={"id": "sfs_legal", "__typename": "Dropdown"},
            ),
        ]
        op = CreateResultsSchemaField(
            "assay",
            BaseFieldProperties(
                name="Comment",
                warehouse_name="comment",
                type=BenchlingFieldType.TEXT,
                required=False,
            ),
            1,
        )
        submitted = submitted_fields(fields, op)
        assert [f["systemName"] for f in submitted] == [
            "notes",
            "comment",
            "old",
            "amount",
        ]
        assert "id" not in submitted[1]
        assert submitted[2]["archived"] is True
        assert submitted[3]["legalTextDropdownId"] == "sfs_legal"

    def test_field_writes_are_refused_on_schemas_with_snapshot_fields(self) -> None:
        fields = [
            field_data("notes"),
            field_data(
                "master",
                derivationType="SNAPSHOT",
                __typename="AnyEntityLinkFieldDefinition",
            ),
        ]
        schemas, listed, submit = patched_schema(fields)
        with schemas, listed, submit as set_fields:
            op = ArchiveResultsSchemaField("assay", "notes")
            with pytest.raises(ValueError, match="snapshot fields"):
                op.validate(Mock())
            with pytest.raises(ValueError, match="snapshot fields"):
                op.execute(Mock())
        set_fields.assert_not_called()

    def test_update_field_changes_only_that_field(self) -> None:
        fields = [field_data("notes"), field_data("comment")]
        op = UpdateResultsSchemaField(
            "assay", "comment", BaseFieldProperties(tooltip="Free text")
        )
        submitted = submitted_fields(fields, op)
        assert submitted[0].get("description") is None
        assert submitted[1]["description"] == "Free text"
        assert submitted[1]["id"] == "fld_comment"

    def test_reorder_fields(self) -> None:
        fields = [field_data("a"), field_data("b"), field_data("c")]
        submitted = submitted_fields(
            fields, ReorderResultsSchemaFields("assay", ["c", "a", "b"])
        )
        assert [f["systemName"] for f in submitted] == ["c", "a", "b"]

    def test_update_schema_sends_name_and_system_name(self) -> None:
        schemas, listed, _ = patched_schema([])
        with schemas, listed, patch(f"{OPS}.update_results_schema") as update:
            UpdateResultsSchema(
                "assay", {"name": "Assay v2", "warehouse_name": "assay_v2"}
            ).execute(Mock())
        assert update.call_args.args[1:] == (
            "assaysch_1",
            {"name": "Assay v2", "systemName": "assay_v2"},
        )


class MockAssayResult(BaseResultsModel):
    __schema_properties__ = ResultsSchemaProperties(
        name="Mock Assay Result", warehouse_name="mock_assay_result"
    )

    notes = Column(name="Notes", type=BenchlingFieldType.TEXT, required=False)
    comment = Column(name="Comment", type=BenchlingFieldType.TEXT, required=False)
    master = Column(
        name="Master",
        type=BenchlingFieldType.ENTITY_LINK,
        required=False,
        is_snapshot=True,
    )


def compare(benchling: list[Any]) -> list[Any]:
    with (
        patch(
            "liminal.results_schemas.compare.get_converted_results_schemas",
            return_value=benchling,
        ),
        patch(
            "liminal.results_schemas.compare.BaseResultsModel.get_all_subclasses",
            return_value=[MockAssayResult],
        ),
    ):
        result = compare_results_schemas(Mock(), {"mock_assay_result"})
    assert result["Archive"] == []
    return [c.op for c in result["mock_assay_result"]]


def benchling_fields(
    **overrides: BaseFieldProperties,
) -> dict[str, BaseFieldProperties]:
    fields = {
        name: column.properties
        for name, column in MockAssayResult.get_columns_dict().items()
    }
    return {**fields, **overrides}


class TestCompareResultsSchemas:
    props = ResultsSchemaProperties(
        name="Mock Assay Result", warehouse_name="mock_assay_result"
    )

    def test_columns_are_the_benchling_fields_only(self) -> None:
        assert list(MockAssayResult.get_columns_dict()) == [
            "notes",
            "comment",
            "master",
        ]

    def test_identical_schema_has_no_operations(self) -> None:
        assert compare([(self.props, benchling_fields())]) == []

    def test_missing_schema_is_created(self) -> None:
        (op,) = compare([])
        assert isinstance(op, CreateResultsSchema)
        assert [f.warehouse_name for f in op.fields] == ["notes", "comment", "master"]

    def test_archived_schema_is_unarchived(self) -> None:
        archived = ResultsSchemaProperties(
            name="Mock Assay Result", warehouse_name="mock_assay_result"
        ).set_archived(True)
        (op,) = compare([(archived, benchling_fields())])
        assert isinstance(op, UnarchiveResultsSchema)

    def test_field_differences(self) -> None:
        fields = benchling_fields(
            notes=BaseFieldProperties(
                name="Notes", type=BenchlingFieldType.TEXT, required=True
            ),
            extra=BaseFieldProperties(
                name="Extra", type=BenchlingFieldType.TEXT, required=False
            ),
        )
        del fields["comment"]
        ops = compare([(self.props, fields)])
        assert [type(op).__name__ for op in ops] == [
            "CreateResultsSchemaField",
            "UpdateResultsSchemaField",
            "ArchiveResultsSchemaField",
        ]
        create, update, archive = ops
        assert create.field_props.warehouse_name == "comment" and create.index == 1
        assert update.update_props.model_dump(exclude_unset=True) == {"required": False}
        assert archive.wh_field_name == "extra"

    def test_reordered_fields(self) -> None:
        fields = benchling_fields()
        reordered = {name: fields[name] for name in ["comment", "notes", "master"]}
        (op,) = compare([(self.props, reordered)])
        assert isinstance(op, ReorderResultsSchemaFields)
        assert op.new_order == ["notes", "comment", "master"]
