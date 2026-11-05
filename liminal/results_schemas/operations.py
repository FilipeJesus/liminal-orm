"""Operations that change result schemas through Benchling's v3 result-schema API.

Result schema fields have the same v3 shape as entity schema fields, so the entity schema field models are reused.
Benchling's field definition input has no way to describe a snapshot (derived) field, and set-field-definitions
replaces the whole field list, so fields are never written on a result schema that has snapshot fields, and
snapshot fields are never created; those changes are made in the Benchling UI.
"""

from __future__ import annotations

from typing import Any, ClassVar

from liminal.base.base_operation import BaseOperation
from liminal.base.properties.base_field_properties import BaseFieldProperties
from liminal.connection import BenchlingService
from liminal.entity_schemas.entity_schema_models import (
    EntitySchemaFieldInputModel,
    EntitySchemaFieldModel,
)
from liminal.enums import BenchlingFieldDefinitionInputType, BenchlingFieldType
from liminal.orm.results_schema_properties import ResultsSchemaProperties
from liminal.results_schemas.api import (
    create_results_schema,
    list_result_schema_field_definitions,
    list_results_schemas,
    set_results_schema_field_definitions,
    update_results_schema,
)


class _ResultsSchema:
    """A result schema and all of its fields, including archived ones, as returned by the v3 API."""

    def __init__(self, data: dict[str, Any], fields: list[EntitySchemaFieldModel]):
        self.id: str = data["id"]
        self.name: str = data["name"]
        self.system_name: str = data["systemName"]
        self.archived: bool = bool(data.get("archived"))
        self.fields = fields

    @classmethod
    def get(
        cls, benchling_service: BenchlingService, wh_schema_name: str
    ) -> _ResultsSchema:
        schema = next(
            (
                s
                for s in list_results_schemas(benchling_service, include_archived=True)
                if s["systemName"] == wh_schema_name
            ),
            None,
        )
        if schema is None:
            raise ValueError(
                f"Result schema {wh_schema_name} not found in Benchling {benchling_service.benchling_tenant}."
            )
        fields = [
            EntitySchemaFieldModel.model_validate(f)
            for f in list_result_schema_field_definitions(
                benchling_service, schema["id"], include_archived=True
            )
        ]
        return cls(schema, fields)

    def get_field(self, wh_field_name: str) -> EntitySchemaFieldModel:
        for field in self.fields:
            if field.systemName == wh_field_name:
                return field
        raise ValueError(
            f"Field '{wh_field_name}' not found on result schema {self.system_name}."
        )

    def check_fields_writable(self) -> None:
        snapshots = [f.systemName for f in self.fields if f.derivationType != "DIRECT"]
        if snapshots:
            raise ValueError(
                f"Result schema {self.system_name} has snapshot fields ({', '.join(snapshots)}). "
                "Benchling's field definition input cannot describe snapshot fields, so change this schema's fields in the Benchling UI."
            )

    def move_field(self, field: EntitySchemaFieldModel, index: int | None) -> None:
        if index is not None:
            self.fields.remove(field)
            self.fields.insert(index, field)

    def set_fields(
        self,
        benchling_service: BenchlingService,
        new_fields: dict[int, EntitySchemaFieldInputModel] | None = None,
    ) -> dict[str, Any]:
        """Submit the complete field list, inserting any new fields at their index."""
        self.check_fields_writable()
        inputs = [EntitySchemaFieldInputModel.from_field_model(f) for f in self.fields]
        for index, new_field in sorted((new_fields or {}).items()):
            inputs.insert(index, new_field)
        return set_results_schema_field_definitions(
            benchling_service,
            self.id,
            [f.model_dump(exclude_none=True) for f in inputs],
        )


def _field_input(
    field_props: BaseFieldProperties, benchling_service: BenchlingService
) -> EntitySchemaFieldInputModel:
    if field_props.is_snapshot:
        raise ValueError(
            f"Field {field_props.warehouse_name} is a snapshot field, which Benchling's API cannot create. Add it in the Benchling UI."
        )
    field_input = EntitySchemaFieldInputModel.from_benchling_props(
        field_props, benchling_service
    )
    # Decimal fields map to FLOAT, which cannot carry a unit; Benchling stores decimal fields with a unit as DECIMAL.
    if field_props.type == BenchlingFieldType.DECIMAL and field_props.unit_name:
        field_input.type = BenchlingFieldDefinitionInputType.DECIMAL
    return field_input


class CreateResultsSchema(BaseOperation):
    order: ClassVar[int] = 161

    def __init__(
        self,
        schema_properties: ResultsSchemaProperties,
        fields: list[BaseFieldProperties],
    ) -> None:
        self.schema_properties = schema_properties
        self.fields = fields

    def execute(self, benchling_service: BenchlingService) -> dict[str, Any]:
        self.validate(benchling_service)
        payload = {
            "organizationId": benchling_service.organization_id,
            "name": self.schema_properties.name,
            "systemName": self.schema_properties.warehouse_name,
            "fieldDefinitions": [
                _field_input(f, benchling_service).model_dump(exclude_none=True)
                for f in self.fields
            ],
        }
        return create_results_schema(benchling_service, payload)

    def validate(self, benchling_service: BenchlingService) -> None:
        for schema in list_results_schemas(benchling_service, include_archived=True):
            if schema["systemName"] == self.schema_properties.warehouse_name:
                raise ValueError(
                    f"Result schema warehouse name {self.schema_properties.warehouse_name} already exists in Benchling."
                )
            if schema["name"] == self.schema_properties.name:
                raise ValueError(
                    f"Result schema name {self.schema_properties.name} already exists in Benchling."
                )
        snapshots = [f.warehouse_name for f in self.fields if f.is_snapshot]
        if snapshots:
            raise ValueError(
                f"Result schema {self.schema_properties.warehouse_name}: snapshot fields ({', '.join(str(s) for s in snapshots)}) cannot be created through the API. Add them in the Benchling UI."
            )

    def describe_operation(self) -> str:
        return f"{self.schema_properties.name}: Creating new result schema with fields: {', '.join([f.warehouse_name for f in self.fields if f.warehouse_name is not None])}."

    def describe(self) -> str:
        return f"{self.schema_properties.name}: Result schema is defined in code but not in Benchling."


class UnarchiveResultsSchema(BaseOperation):
    order: ClassVar[int] = 162

    def __init__(self, wh_schema_name: str) -> None:
        self.wh_schema_name = wh_schema_name

    def execute(self, benchling_service: BenchlingService) -> dict[str, Any]:
        schema = _ResultsSchema.get(benchling_service, self.wh_schema_name)
        if not schema.archived:
            raise ValueError(
                f"Result schema {self.wh_schema_name} is already unarchived in Benchling."
            )
        return update_results_schema(benchling_service, schema.id, {"archived": False})

    def describe_operation(self) -> str:
        return f"{self.wh_schema_name}: Unarchiving result schema."

    def describe(self) -> str:
        return f"{self.wh_schema_name}: Result schema is archived in Benchling but is defined in code again."


class CreateResultsSchemaField(BaseOperation):
    order: ClassVar[int] = 163

    def __init__(
        self,
        wh_schema_name: str,
        field_props: BaseFieldProperties,
        index: int,
    ) -> None:
        self.wh_schema_name = wh_schema_name
        self.field_props = field_props
        self.index = index
        if not field_props.warehouse_name:
            raise ValueError("Field warehouse name is required.")
        if not field_props.name:
            raise ValueError("Field name is required.")
        self._wh_field_name: str = field_props.warehouse_name

    def execute(self, benchling_service: BenchlingService) -> dict[str, Any]:
        schema = self._validate(benchling_service)
        return schema.set_fields(
            benchling_service,
            {self.index: _field_input(self.field_props, benchling_service)},
        )

    def validate(self, benchling_service: BenchlingService) -> None:
        self._validate(benchling_service)

    def _validate(self, benchling_service: BenchlingService) -> _ResultsSchema:
        schema = _ResultsSchema.get(benchling_service, self.wh_schema_name)
        schema.check_fields_writable()
        existing = next(
            (f for f in schema.fields if f.systemName == self._wh_field_name), None
        )
        if existing:
            raise ValueError(
                f"Field {self._wh_field_name} already exists on result schema {self.wh_schema_name} and is {'archived' if existing.archived else 'active'} in Benchling."
            )
        if self.field_props.is_snapshot:
            raise ValueError(
                f"Field {self._wh_field_name} on result schema {self.wh_schema_name} is a snapshot field, which Benchling's API cannot create. Add it in the Benchling UI."
            )
        return schema

    def describe_operation(self) -> str:
        return f"{self.wh_schema_name}: Creating result schema field '{self._wh_field_name}' at index {self.index}."

    def describe(self) -> str:
        return f"{self.wh_schema_name}: Result schema field '{self._wh_field_name}' is not defined in Benchling but is defined in code."


class UpdateResultsSchema(BaseOperation):
    order: ClassVar[int] = 164

    def __init__(self, wh_schema_name: str, update_props: dict[str, Any]) -> None:
        """update_props holds the changed ResultsSchemaProperties: name and/or warehouse_name."""
        self.wh_schema_name = wh_schema_name
        self.update_props = update_props

    def execute(self, benchling_service: BenchlingService) -> dict[str, Any]:
        schema = _ResultsSchema.get(benchling_service, self.wh_schema_name)
        payload: dict[str, Any] = {}
        if "name" in self.update_props:
            payload["name"] = self.update_props["name"]
        if "warehouse_name" in self.update_props:
            payload["systemName"] = self.update_props["warehouse_name"]
        return update_results_schema(benchling_service, schema.id, payload)

    def validate(self, benchling_service: BenchlingService) -> None:
        unsupported = set(self.update_props) - {"name", "warehouse_name"}
        if unsupported:
            raise ValueError(
                f"Result schema {self.wh_schema_name}: cannot update {', '.join(sorted(unsupported))}."
            )

    def describe_operation(self) -> str:
        return f"{self.wh_schema_name}: Updating result schema properties to: {self.update_props}."

    def describe(self) -> str:
        return f"{self.wh_schema_name}: Result schema properties are different in code versus Benchling: {self.update_props}."


class UnarchiveResultsSchemaField(BaseOperation):
    order: ClassVar[int] = 165

    def __init__(
        self, wh_schema_name: str, wh_field_name: str, index: int | None = None
    ) -> None:
        self.wh_schema_name = wh_schema_name
        self.wh_field_name = wh_field_name
        self.index = index

    def execute(self, benchling_service: BenchlingService) -> dict[str, Any]:
        schema = _ResultsSchema.get(benchling_service, self.wh_schema_name)
        field = schema.get_field(self.wh_field_name)
        if not field.archived:
            raise ValueError(
                f"Field '{self.wh_field_name}' on result schema {self.wh_schema_name} is not archived."
            )
        field.archived = False
        field.archiveReason = None
        schema.move_field(field, self.index)
        return schema.set_fields(benchling_service)

    def validate(self, benchling_service: BenchlingService) -> None:
        _ResultsSchema.get(
            benchling_service, self.wh_schema_name
        ).check_fields_writable()

    def describe_operation(self) -> str:
        return f"{self.wh_schema_name}: Unarchiving result schema field '{self.wh_field_name}'."

    def describe(self) -> str:
        return f"{self.wh_schema_name}: Result schema field '{self.wh_field_name}' is archived in Benchling but is defined in code again."


class UpdateResultsSchemaField(BaseOperation):
    order: ClassVar[int] = 166

    def __init__(
        self,
        wh_schema_name: str,
        wh_field_name: str,
        update_props: BaseFieldProperties,
    ) -> None:
        self.wh_schema_name = wh_schema_name
        self.wh_field_name = wh_field_name
        self.update_props = update_props

    def execute(self, benchling_service: BenchlingService) -> dict[str, Any]:
        schema = self._validate(benchling_service)
        schema.get_field(self.wh_field_name).update_from_props(
            self.update_props.model_dump(exclude_unset=True), benchling_service
        )
        return schema.set_fields(benchling_service)

    def validate(self, benchling_service: BenchlingService) -> None:
        self._validate(benchling_service)

    def _validate(self, benchling_service: BenchlingService) -> _ResultsSchema:
        schema = _ResultsSchema.get(benchling_service, self.wh_schema_name)
        schema.check_fields_writable()
        update = self.update_props.model_dump(exclude_unset=True)
        if "is_snapshot" in update:
            raise ValueError(
                f"Field {self.wh_field_name} on result schema {self.wh_schema_name}: a field cannot be changed to or from a snapshot field through the API."
            )
        if "unit_name" in update and schema.get_field(self.wh_field_name).unit:
            raise ValueError(
                f"Field {self.wh_field_name} on result schema {self.wh_schema_name}: the unit of a field cannot be changed once it has been set."
            )
        if self.update_props.name and any(
            f.name == self.update_props.name for f in schema.fields
        ):
            raise ValueError(
                f"New field name {self.update_props.name} already exists on result schema {self.wh_schema_name}."
            )
        return schema

    def describe_operation(self) -> str:
        return f"{self.wh_schema_name}: Updating result schema field '{self.wh_field_name}': {str(self.update_props)}."

    def describe(self) -> str:
        return f"{self.wh_schema_name}: Result schema field '{self.wh_field_name}' in Benchling is different than in code: {str(self.update_props)}."


class ArchiveResultsSchemaField(BaseOperation):
    order: ClassVar[int] = 167

    def __init__(
        self, wh_schema_name: str, wh_field_name: str, index: int | None = None
    ) -> None:
        self.wh_schema_name = wh_schema_name
        self.wh_field_name = wh_field_name
        self.index = index

    def execute(self, benchling_service: BenchlingService) -> dict[str, Any]:
        schema = _ResultsSchema.get(benchling_service, self.wh_schema_name)
        field = schema.get_field(self.wh_field_name)
        if field.archived:
            raise ValueError(
                f"Field '{self.wh_field_name}' on result schema {self.wh_schema_name} is already archived."
            )
        field.archived = True
        field.archiveReason = "Made in error"
        schema.move_field(field, self.index)
        return schema.set_fields(benchling_service)

    def validate(self, benchling_service: BenchlingService) -> None:
        _ResultsSchema.get(
            benchling_service, self.wh_schema_name
        ).check_fields_writable()

    def describe_operation(self) -> str:
        return f"{self.wh_schema_name}: Archiving result schema field '{self.wh_field_name}'."

    def describe(self) -> str:
        return f"{self.wh_schema_name}: Result schema field '{self.wh_field_name}' is defined in Benchling but not in code."


class ReorderResultsSchemaFields(BaseOperation):
    order: ClassVar[int] = 168

    def __init__(self, wh_schema_name: str, new_order: list[str]) -> None:
        self.wh_schema_name = wh_schema_name
        self.new_order = new_order

    def execute(self, benchling_service: BenchlingService) -> dict[str, Any]:
        schema = _ResultsSchema.get(benchling_service, self.wh_schema_name)
        position = {name: index for index, name in enumerate(self.new_order)}
        ordered = sorted(
            (f for f in schema.fields if f.systemName in position),
            key=lambda f: position[f.systemName],
        )
        schema.fields = ordered + [
            f for f in schema.fields if f.systemName not in position
        ]
        return schema.set_fields(benchling_service)

    def validate(self, benchling_service: BenchlingService) -> None:
        _ResultsSchema.get(
            benchling_service, self.wh_schema_name
        ).check_fields_writable()

    def describe_operation(self) -> str:
        return f"{self.wh_schema_name}: Reordering result schema fields."

    def describe(self) -> str:
        return f"{self.wh_schema_name}: Order of result schema fields is different in code versus Benchling."


class ArchiveResultsSchema(BaseOperation):
    order: ClassVar[int] = 169

    def __init__(self, wh_schema_name: str) -> None:
        self.wh_schema_name = wh_schema_name

    def execute(self, benchling_service: BenchlingService) -> dict[str, Any]:
        schema = _ResultsSchema.get(benchling_service, self.wh_schema_name)
        if schema.archived:
            raise ValueError(
                f"Result schema {self.wh_schema_name} is already archived in Benchling."
            )
        return update_results_schema(
            benchling_service,
            schema.id,
            {"archived": True, "archiveReason": "Made in error"},
        )

    def describe_operation(self) -> str:
        return f"{self.wh_schema_name}: Archiving result schema."

    def describe(self) -> str:
        return f"{self.wh_schema_name}: Result schema is defined in Benchling but not in code anymore."
