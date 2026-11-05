from __future__ import annotations

from liminal.base.compare_operation import CompareOperation
from liminal.base.properties.base_field_properties import BaseFieldProperties
from liminal.connection import BenchlingService
from liminal.orm.base_results_model import BaseResultsModel
from liminal.results_schemas.operations import (
    ArchiveResultsSchema,
    ArchiveResultsSchemaField,
    CreateResultsSchema,
    CreateResultsSchemaField,
    ReorderResultsSchemaFields,
    UnarchiveResultsSchema,
    UnarchiveResultsSchemaField,
    UpdateResultsSchema,
    UpdateResultsSchemaField,
)
from liminal.results_schemas.utils import get_converted_results_schemas


def compare_results_schemas(
    benchling_service: BenchlingService, schema_names: set[str] | None = None
) -> dict[str, list[CompareOperation]]:
    """Compares the result schemas in Benchling with the result schema models defined in code.
    Returns the operations that bring Benchling in line with the code, keyed by model warehouse name;
    schemas in Benchling that have no model are listed under "Archive".
    If schema_names is given, only those schemas are compared, on both sides.
    """
    models = [
        m
        for m in BaseResultsModel.get_all_subclasses(schema_names)
        if not m.__schema_properties__._archived
    ]
    benchling_schemas = {
        props.warehouse_name: (props, fields)
        for props, fields in get_converted_results_schemas(
            benchling_service, include_archived=True, wh_schema_names=schema_names
        )
    }
    model_operations: dict[str, list[CompareOperation]] = {}
    for model in models:
        wh_name = model.__schema_properties__.warehouse_name
        columns = {
            name: column.properties for name, column in model.get_columns_dict().items()
        }
        ops: list[CompareOperation] = []
        if wh_name not in benchling_schemas:
            ops.append(
                CompareOperation(
                    op=CreateResultsSchema(
                        model.__schema_properties__,
                        [
                            BaseFieldProperties(
                                **props.model_dump(exclude_unset=True)
                            ).set_warehouse_name(name)
                            for name, props in columns.items()
                        ],
                    ),
                    reverse_op=ArchiveResultsSchema(wh_name),
                )
            )
            model_operations[wh_name] = ops
            continue

        benchling_props, benchling_fields = benchling_schemas[wh_name]
        if benchling_props._archived:
            ops.append(
                CompareOperation(
                    op=UnarchiveResultsSchema(wh_name),
                    reverse_op=ArchiveResultsSchema(wh_name),
                )
            )
        schema_diff = {
            k: v
            for k, v in model.__schema_properties__.model_dump().items()
            if getattr(benchling_props, k) != v
        }
        if schema_diff:
            ops.append(
                CompareOperation(
                    op=UpdateResultsSchema(wh_name, schema_diff),
                    reverse_op=UpdateResultsSchema(
                        wh_name,
                        {k: getattr(benchling_props, k) for k in schema_diff},
                    ),
                )
            )
        active = {k: v for k, v in benchling_fields.items() if not v._archived}
        archived = {k: v for k, v in benchling_fields.items() if v._archived}
        for name, benchling_field in active.items():
            if name not in columns:
                ops.append(
                    CompareOperation(
                        op=ArchiveResultsSchemaField(wh_name, name),
                        reverse_op=UnarchiveResultsSchemaField(wh_name, name),
                    )
                )
            elif columns[name] != benchling_field:
                ops.append(
                    CompareOperation(
                        op=UpdateResultsSchemaField(
                            wh_name,
                            name,
                            BaseFieldProperties(**benchling_field.merge(columns[name])),
                        ),
                        reverse_op=UpdateResultsSchemaField(
                            wh_name,
                            name,
                            BaseFieldProperties(**columns[name].merge(benchling_field)),
                        ),
                    )
                )
        order = list(columns)
        for name in order:
            if name in active:
                continue
            index = order.index(name)
            if name in archived:
                ops.append(
                    CompareOperation(
                        op=UnarchiveResultsSchemaField(wh_name, name, index),
                        reverse_op=ArchiveResultsSchemaField(wh_name, name, index),
                    )
                )
                if archived[name] != columns[name]:
                    ops.append(
                        CompareOperation(
                            op=UpdateResultsSchemaField(
                                wh_name,
                                name,
                                BaseFieldProperties(
                                    **archived[name].merge(columns[name])
                                ),
                            ),
                            reverse_op=UpdateResultsSchemaField(
                                wh_name,
                                name,
                                BaseFieldProperties(
                                    **columns[name].merge(archived[name])
                                ),
                            ),
                        )
                    )
            else:
                ops.append(
                    CompareOperation(
                        op=CreateResultsSchemaField(
                            wh_name,
                            BaseFieldProperties(
                                **columns[name].model_dump(exclude_unset=True)
                            ).set_warehouse_name(name),
                            index,
                        ),
                        reverse_op=ArchiveResultsSchemaField(wh_name, name, index),
                    )
                )
        kept_benchling = [f for f in benchling_fields if f in columns]
        kept_model = [f for f in order if f in kept_benchling]
        if kept_model != kept_benchling:
            ops.append(
                CompareOperation(
                    op=ReorderResultsSchemaFields(wh_name, order),
                    reverse_op=ReorderResultsSchemaFields(wh_name, kept_benchling),
                )
            )
        model_operations[wh_name] = ops

    model_names = {m.__schema_properties__.warehouse_name for m in models}
    model_operations["Archive"] = [
        CompareOperation(
            op=ArchiveResultsSchema(name),
            reverse_op=UnarchiveResultsSchema(name),
        )
        for name, (props, _) in sorted(benchling_schemas.items())
        if name not in model_names and not props._archived
    ]
    return {k: sorted(v) for k, v in model_operations.items()}
