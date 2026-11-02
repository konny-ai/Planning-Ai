# Copertura campi del workbook

Generato con `python tools/workbook_coverage.py`; verificare con `--check`.

59 tabelle e 382 campi raccolti; 119 campi canonici hanno esattamente una collocazione.

La mappatura non implica che ogni estensione sia calcolabile. Limiti e
distinzione fra dati operativi e contesto: [planning-workbook.md](planning-workbook.md).

| Campo canonico | Tabella Excel | Colonna stabile | Unità Excel |
| --- | --- | --- | --- |
| `allocation_rank.operation_id` | 37 Ordine materiali | `operation_id` | — |
| `allocation_rank.rank` | 37 Ordine materiali | `rank` | — |
| `calendar.id` | 20 Calendari | `id` | — |
| `calendar.timezone` | 20 Calendari | `timezone` | — |
| `calendar_closure.calendar_id` | 25 Fermi calendario | `calendar_id` | — |
| `calendar_closure.end` | 25 Fermi calendario | `end` | — |
| `calendar_closure.start` | 25 Fermi calendario | `start` | — |
| `calendar_exception.calendar_id` | 23 Giorni speciali | `calendar_id` | — |
| `calendar_exception.day` | 23 Giorni speciali | `day` | — |
| `calendar_exception_shift.calendar_id` | 24 Orari speciali | `calendar_id` | — |
| `calendar_exception_shift.day` | 24 Orari speciali | `day` | — |
| `calendar_exception_shift.end` | 24 Orari speciali | `end` | — |
| `calendar_exception_shift.start` | 24 Orari speciali | `start` | — |
| `calendar_holiday.calendar_id` | 22 Giorni chiusi | `calendar_id` | — |
| `calendar_holiday.day` | 22 Giorni chiusi | `day` | — |
| `calendar_shift.calendar_id` | 21 Turni | `calendar_id` | — |
| `calendar_shift.end` | 21 Turni | `end` | — |
| `calendar_shift.start` | 21 Turni | `start` | — |
| `calendar_shift.weekday` | 21 Turni | `weekday` | — |
| `identity_map.canonical_id` | 52 Corrispondenze | `canonical_id` | — |
| `identity_map.external_id` | 52 Corrispondenze | `external_id` | — |
| `identity_map.map` | 52 Corrispondenze | `map` | — |
| `material_need.item_id` | 36 Fabbisogni materiali | `item_id` | — |
| `material_need.operation_id` | 36 Fabbisogni materiali | `operation_id` | — |
| `material_need.quantity` | 36 Fabbisogni materiali | `quantity` | — |
| `material_supply.available_at` | 35 Disponibilita materiali | `available_at` | — |
| `material_supply.id` | 35 Disponibilita materiali | `id` | — |
| `material_supply.item_id` | 35 Disponibilita materiali | `item_id` | — |
| `material_supply.kind` | 35 Disponibilita materiali | `kind` | — |
| `material_supply.quantity` | 35 Disponibilita materiali | `quantity` | — |
| `material_supply.reference` | 35 Disponibilita materiali | `reference` | — |
| `nest.duration_seconds` | 39 Nesting | `duration_seconds` | minuti |
| `nest.id` | 39 Nesting | `id` | — |
| `nest.material` | 39 Nesting | `material` | — |
| `nest.preview` | 39 Nesting | `preview` | — |
| `nest.resource_id` | 39 Nesting | `resource_id` | — |
| `nest.setup_seconds` | 39 Nesting | `setup_seconds` | minuti |
| `nest.sheets` | 39 Nesting | `sheets` | — |
| `nest.status` | 39 Nesting | `status` | — |
| `nest.thickness_mm` | 39 Nesting | `thickness_mm` | mm |
| `nest.version` | 39 Nesting | `version` | — |
| `nest_member.nest_id` | 40 Membri nesting | `nest_id` | — |
| `nest_member.operation_id` | 40 Membri nesting | `operation_id` | — |
| `nest_member.quantity` | 40 Membri nesting | `quantity` | — |
| `operation.customer_priority` | 09 Fasi | `customer_priority` | — |
| `operation.due` | 09 Fasi | `due` | — |
| `operation.duration_seconds` | 09 Fasi | `duration_seconds` | minuti |
| `operation.id` | 09 Fasi | `id` | — |
| `operation.material` | 09 Fasi | `material` | — |
| `operation.mode` | 09 Fasi | `mode` | — |
| `operation.order_id` | 09 Fasi | `order_id` | — |
| `operation.phase_priority` | 09 Fasi | `phase_priority` | — |
| `operation.preferred_alternative` | 09 Fasi | `preferred_alternative` | — |
| `operation.priority` | 09 Fasi | `priority` | — |
| `operation.quantity` | 09 Fasi | `quantity` | — |
| `operation.release` | 09 Fasi | `release` | — |
| `operation.setup_seconds` | 09 Fasi | `setup_seconds` | minuti |
| `operation.splittable` | 09 Fasi | `splittable` | — |
| `operation.thickness_mm` | 09 Fasi | `thickness_mm` | mm |
| `operation_demand.alternative` | 15 Alternative risorse | `alternative` | — |
| `operation_demand.operation_id` | 14 Risorse delle fasi | `operation_id` | — |
| `operation_demand.resource_id` | 15 Alternative risorse | `resource_id` | — |
| `operation_demand.units` | 15 Alternative risorse | `units` | — |
| `operation_dependency.kind` | 10 Precedenze | `kind` | — |
| `operation_dependency.lag_seconds` | 10 Precedenze | `lag_seconds` | minuti |
| `operation_dependency.max_lag_seconds` | 10 Precedenze | `max_lag_seconds` | minuti |
| `operation_dependency.operation_id` | 10 Precedenze | `operation_id` | — |
| `operation_dependency.predecessor_id` | 10 Precedenze | `predecessor_id` | — |
| `operation_external_end.external_end` | 42 Rientri fornitori | `external_end` | — |
| `operation_external_end.operation_id` | 42 Rientri fornitori | `operation_id` | — |
| `operation_progress.completed_at` | 30 Avanzamento | `completed_at` | — |
| `operation_progress.operation_id` | 30 Avanzamento | `operation_id` | — |
| `operation_progress.produced_quantity` | 30 Avanzamento | `produced_quantity` | — |
| `operation_progress.recorded_setup_seconds` | 30 Avanzamento | `recorded_setup_seconds` | minuti |
| `operation_progress.recorded_work_seconds` | 30 Avanzamento | `recorded_work_seconds` | minuti |
| `operation_progress.resource_id` | 30 Avanzamento | `resource_id` | — |
| `operation_progress.status` | 30 Avanzamento | `status` | — |
| `operation_state.completed_at` | 31 Stati e blocchi | `completed_at` | — |
| `operation_state.fixed_alternative` | 31 Stati e blocchi | `fixed_alternative` | — |
| `operation_state.fixed_start` | 31 Stati e blocchi | `fixed_start` | — |
| `operation_state.operation_id` | 31 Stati e blocchi | `operation_id` | — |
| `order.customer_id` | 05 Commesse | `customer_id` | — |
| `order.customer_priority` | 05 Commesse | `customer_priority` | — |
| `order.due` | 05 Commesse | `due` | — |
| `order.id` | 05 Commesse | `id` | — |
| `order.mode` | 05 Commesse | `mode` | — |
| `order.priority` | 05 Commesse | `priority` | — |
| `order.release` | 05 Commesse | `release` | — |
| `profile.alternative_switch_min_gain_seconds` | 45 Profili di calcolo | `alternative_switch_min_gain_seconds` | minuti |
| `profile.auto_backward_days` | 45 Profili di calcolo | `auto_backward_days` | — |
| `profile.auto_rule` | 45 Profili di calcolo | `auto_rule` | — |
| `profile.auto_slack_seconds` | 45 Profili di calcolo | `auto_slack_seconds` | minuti |
| `profile.customer_weight` | 45 Profili di calcolo | `customer_weight` | — |
| `profile.delivery_buffer_seconds` | 45 Profili di calcolo | `delivery_buffer_seconds` | minuti |
| `profile.due_weight` | 45 Profili di calcolo | `due_weight` | — |
| `profile.id` | 45 Profili di calcolo | `id` | — |
| `profile.mode` | 45 Profili di calcolo | `mode` | — |
| `profile.priority_weight` | 45 Profili di calcolo | `priority_weight` | — |
| `profile.wip_priority_bonus` | 45 Profili di calcolo | `wip_priority_bonus` | — |
| `resource.calendar_id` | 12 Risorse | `calendar_id` | — |
| `resource.capacity` | 12 Risorse | `capacity` | — |
| `resource.efficiency_bp` | 12 Risorse | `efficiency_bp` | punti base; 10000 = 100% |
| `resource.id` | 12 Risorse | `id` | — |
| `resource.kind` | 12 Risorse | `kind` | — |
| `rule.action` | 46 Regole con ambito | `action` | — |
| `rule.confirmed_at` | 46 Regole con ambito | `confirmed_at` | — |
| `rule.confirmed_by` | 46 Regole con ambito | `confirmed_by` | — |
| `rule.customer_id` | 46 Regole con ambito | `customer_id` | — |
| `rule.id` | 46 Regole con ambito | `id` | — |
| `rule.material` | 46 Regole con ambito | `material` | — |
| `rule.max_thickness_mm` | 46 Regole con ambito | `max_thickness_mm` | mm |
| `rule.min_thickness_mm` | 46 Regole con ambito | `min_thickness_mm` | mm |
| `rule.note` | 46 Regole con ambito | `note` | — |
| `rule.order_id` | 46 Regole con ambito | `order_id` | — |
| `rule.profile` | 46 Regole con ambito | `profile` | — |
| `rule.resource_id` | 46 Regole con ambito | `resource_id` | — |
| `rule.valid_from` | 46 Regole con ambito | `valid_from` | — |
| `rule.valid_to` | 46 Regole con ambito | `valid_to` | — |
| `rule.value` | 46 Regole con ambito | `value` | dipende dall’azione; temporali in secondi |

La domanda `operation_demand` è espansa dalla configurazione condivisa
`phase_group` → `resource_group` → `group_demand`; non viene duplicata su
ogni fase nell’Excel. I tempi canonici in secondi sono esposti in minuti.
