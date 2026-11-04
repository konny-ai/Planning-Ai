"""Minimal JSON Schema checker (stdlib only) for the subset the contracts/ schemas use:
type, enum, const, required, properties, additionalProperties, items, minItems,
minimum, maximum and local $ref. Returns error strings instead of raising."""


def _type_ok(value, name):
    if name == 'object':
        return isinstance(value, dict)
    if name == 'array':
        return isinstance(value, list)
    if name == 'string':
        return isinstance(value, str)
    if name == 'integer':
        return isinstance(value, int) and not isinstance(value, bool)
    if name == 'number':
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == 'boolean':
        return isinstance(value, bool)
    if name == 'null':
        return value is None
    raise ValueError(f'unsupported type {name}')


def validate(instance, schema, root=None, path='$'):
    root = root or schema
    errors = []
    types = schema.get('type')
    if types is not None:
        names = [types] if isinstance(types, str) else list(types)
        if not any(_type_ok(instance, n) for n in names):
            return [f'{path}: expected {"|".join(names)}, got {type(instance).__name__}']
    if '$ref' in schema:
        if instance is None and isinstance(types, list) and 'null' in types:
            return []
        target = root
        for part in schema['$ref'].removeprefix('#/').split('/'):
            target = target[part]
        errors += validate(instance, target, root, path)
    if 'const' in schema and instance != schema['const']:
        errors.append(f'{path}: expected {schema["const"]!r}')
    if 'enum' in schema and instance not in schema['enum']:
        errors.append(f'{path}: {instance!r} not in {schema["enum"]}')
    if isinstance(instance, dict):
        for key in schema.get('required', ()):
            if key not in instance:
                errors.append(f'{path}: missing {key}')
        props = schema.get('properties', {})
        extra = schema.get('additionalProperties', True)
        for key, value in instance.items():
            if key in props:
                errors += validate(value, props[key], root, f'{path}.{key}')
            elif extra is False:
                errors.append(f'{path}: unexpected {key}')
            elif isinstance(extra, dict):
                errors += validate(value, extra, root, f'{path}.{key}')
    if isinstance(instance, list):
        if len(instance) < schema.get('minItems', 0):
            errors.append(f'{path}: fewer than {schema["minItems"]} items')
        if 'items' in schema:
            for i, item in enumerate(instance):
                errors += validate(item, schema['items'], root, f'{path}[{i}]')
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if 'minimum' in schema and instance < schema['minimum']:
            errors.append(f'{path}: below {schema["minimum"]}')
        if 'maximum' in schema and instance > schema['maximum']:
            errors.append(f'{path}: above {schema["maximum"]}')
    return errors
