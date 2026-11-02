"""Local complete-workbook commands. Inputs are never overwritten."""
import json
from pathlib import Path

from planning_factory.adapters.json_io import dumps, loads, primitive
from planning_factory.adapters.planning_workbook import (
    export_binding, export_scenario, load_workbook, summary,
    include_factory_source, template_tables,
)
from planning_factory.canonical_cli import _distinct
from planning_factory.domain.validation import ValidationError

COMMANDS=('workbook-template','workbook-export','workbook-validate','workbook-plan')


def register(sub):
    template=sub.add_parser('workbook-template',help='Crea il modello Excel completo, con configurazioni da completare')
    template.add_argument('-o','--output',required=True)
    export=sub.add_parser('workbook-export',help='Esporta scenario o binding in un Excel completo e autonomo')
    export.add_argument('source')
    export.add_argument('-o','--output',required=True)
    export.add_argument('--now',required=True,help='Istante esplicito con fuso della fotografia esportata')
    export.add_argument('--data-nature',choices=('effettivi','stimati','simulati','misti'),default='misti')
    export.add_argument('--factory-source',help='Export Factory ricco da conservare come evidenza, senza dedurre stati o abilitazioni setup')
    validate=sub.add_parser('workbook-validate',help='Controlla versione, tabelle, dati e vincoli del workbook')
    validate.add_argument('workbook')
    validate.add_argument('--report')
    schedule=sub.add_parser('workbook-plan',help='Importa e pianifica un workbook completo con audit indipendente')
    schedule.add_argument('workbook')
    schedule.add_argument('-o','--output',required=True,help='Piano verificato JSON')
    schedule.add_argument('--scenario-out',required=True,help='Scenario effettivo cui si riferisce il piano')
    schedule.add_argument('--report',required=True,help='Provenienza, percorso e prenotazioni setup/run')
    schedule.add_argument('--html',help='Gantt offline del piano verificato')


def run(args):
    if args.command=='workbook-template':
        from planning_factory.adapters.workbook_io import write_tables
        write_tables(args.output,template_tables())
        print(json.dumps({'output':str(Path(args.output)),'incomplete_template':True}))
        return 0
    if args.command=='workbook-export':
        _distinct([args.output],[args.source])
        raw=json.loads(Path(args.source).read_text(encoding='utf-8'))
        if {'installation','sources','domains'}<=raw.keys():
            tables,built=export_binding(args.source,args.output,now=args.now,data_nature=args.data_nature)
        else:
            export_scenario(loads(Path(args.source).read_text(encoding='utf-8')),args.output,
                            extracted_at=args.now,data_nature=args.data_nature)
        if args.factory_source:
            from planning_factory.adapters.workbook_io import read_tables,write_tables
            tables,_,_=read_tables(args.output)
            tables=include_factory_source(tables,json.loads(Path(args.factory_source).read_text(encoding='utf-8')))
            write_tables(args.output,tables)
        print(json.dumps({'output':str(Path(args.output)),'self_contained':True,'format':'planning-workbook-v1'}))
        return 0
    outputs=[getattr(args,k,None) for k in ('output','scenario_out','report','html')]
    _distinct([p for p in outputs if p],[args.workbook])
    workbook=load_workbook(args.workbook)
    report=summary(workbook)
    if args.command=='workbook-validate':
        if args.report:
            Path(args.report).write_text(dumps(report),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))
        return 0 if workbook.validation_accepted else 2
    scenario,result,evidence=workbook.calculate()
    report['calculation']=evidence
    from planning_factory.adapters.json_io import scenario_digest
    report['calculated_scenario_digest']=scenario_digest(scenario)
    Path(args.scenario_out).write_text(dumps(scenario),encoding='utf-8')
    Path(args.output).write_text(dumps(result),encoding='utf-8')
    Path(args.report).write_text(dumps(report),encoding='utf-8')
    if args.html:
        from planning_factory.adapters.reports import html_report
        Path(args.html).write_text(html_report(scenario,result,capacity_scale=evidence.get('capacity_scale',1)),encoding='utf-8')
    print(json.dumps({'scenario_digest':result.scenario_digest,'audited':True,
                      'unscheduled':len(result.unscheduled),'planning_path':report['planning_path']}))
    return 2 if result.unscheduled else 0
