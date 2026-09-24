import { useEffect, useState } from 'react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'

type JsonSchema = {
  type?: string | string[]
  enum?: unknown[]
  properties?: Record<string, JsonSchema>
  title?: string
  description?: string
  default?: unknown
}

export function defaultsForSchema(schema: JsonSchema): Record<string, unknown> {
  const properties = schema.properties || {}
  const defaults: Record<string, unknown> = {}
  for (const [name, field] of Object.entries(properties)) {
    if (field.default !== undefined) defaults[name] = field.default
  }
  return defaults
}

export function matchEnumValue(options: unknown[], raw: string): unknown {
  const match = options.find((option) => String(option) === raw)
  return match === undefined ? raw : match
}

function simpleType(schema: JsonSchema): 'string' | 'number' | 'integer' | 'boolean' | 'enum' | 'object' | 'unsupported' {
  if (Array.isArray(schema.enum)) return 'enum'
  const declared = Array.isArray(schema.type) ? schema.type.filter((item) => item !== 'null') : [schema.type]
  const type = declared.length === 1 ? declared[0] : undefined
  if (type === 'string' || type === 'number' || type === 'integer' || type === 'boolean') return type
  if (type === 'object' && schema.properties) return 'object'
  return 'unsupported'
}

function displayed(current: unknown, field: JsonSchema): unknown {
  return current === undefined ? field.default : current
}

function JsonDraftEditor({
  label,
  value,
  onParsed,
  onInvalid,
  objectOnly,
}: {
  label: string
  value: unknown
  onParsed: (parsed: unknown) => void
  onInvalid: (message: string | null, path?: string) => void
  objectOnly: boolean
}) {
  const serialized = JSON.stringify(value ?? (objectOnly ? {} : null), null, 2)
  const [text, setText] = useState(serialized)

  useEffect(() => {
    setText(serialized)
  }, [serialized])

  return (
    <textarea
      aria-label={label}
      className="w-full min-h-28 rounded-md border bg-background p-2 text-xs font-mono"
      value={text}
      onChange={(event) => {
        const next = event.target.value
        setText(next)
        try {
          const parsed = JSON.parse(next) as unknown
          if (objectOnly && (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))) {
            onInvalid('Agent config must be a JSON object')
            return
          }
          onInvalid(null)
          onParsed(parsed)
        } catch (cause) {
          onInvalid(cause instanceof Error ? cause.message : 'Invalid JSON')
        }
      }}
    />
  )
}

export function SchemaForm({
  schema,
  value,
  onChange,
  onInvalid,
}: {
  schema: JsonSchema
  value: Record<string, unknown>
  onChange: (next: Record<string, unknown>) => void
  onInvalid: (message: string | null, path?: string) => void
}) {
  if (simpleType(schema) !== 'object' || !schema.properties) {
    return (
      <JsonDraftEditor
        label="Agent config JSON"
        value={value}
        objectOnly
        onInvalid={onInvalid}
        onParsed={(parsed) => onChange(parsed as Record<string, unknown>)}
      />
    )
  }

  return (
    <div className="space-y-3">
      {Object.entries(schema.properties).map(([name, field]) => {
        const kind = simpleType(field)
        const current = displayed(value[name], field)
        const setField = (next: unknown) => onChange({ ...value, [name]: next })
        if (kind === 'boolean') {
          return (
            <div key={name} className="flex items-center justify-between gap-3">
              <Label htmlFor={`schema-${name}`}>{field.title || name}</Label>
              <Switch
                id={`schema-${name}`}
                checked={current === true}
                onCheckedChange={(checked) => setField(checked)}
              />
            </div>
          )
        }
        if (kind === 'enum') {
          return (
            <label key={name} className="block space-y-1 text-sm">
              <span>{field.title || name}</span>
              <select
                aria-label={name}
                className="w-full rounded-md border bg-background p-2"
                value={current === undefined || current === null ? '' : String(current)}
                onChange={(event) => {
                  const raw = event.target.value
                  setField(raw === '' ? null : matchEnumValue(field.enum || [], raw))
                }}
              >
                <option value="">Select</option>
                {(field.enum || []).map((option) => (
                  <option key={String(option)} value={String(option)}>{String(option)}</option>
                ))}
              </select>
            </label>
          )
        }
        if (kind === 'string' || kind === 'number' || kind === 'integer') {
          return (
            <label key={name} className="block space-y-1 text-sm">
              <span>{field.title || name}</span>
              <Input
                aria-label={name}
                type={kind === 'string' ? 'text' : 'number'}
                value={current === undefined || current === null ? '' : String(current)}
                onChange={(event) => {
                  const raw = event.target.value
                  if (kind === 'string') setField(raw)
                  else if (raw === '') setField(null)
                  else setField(kind === 'integer' ? Number.parseInt(raw, 10) : Number(raw))
                }}
              />
            </label>
          )
        }
        return (
          <label key={name} className="block space-y-1 text-sm">
            <span>{field.title || name}</span>
            <JsonDraftEditor
              label={`${name} JSON`}
              value={current ?? null}
              objectOnly={false}
              onInvalid={(message) => onInvalid(message, name)}
              onParsed={setField}
            />
          </label>
        )
      })}
    </div>
  )
}
