import { useRef, useState, type ReactNode } from "react"

import { Input } from "@/components/ui/input"
import type { ModelOption } from "@/lib/api/settings"

export type { ModelOption }

function formatContext(tokens: number): string {
  if (tokens >= 1_000_000) {
    const m = tokens / 1_000_000
    return `${Number.isInteger(m) ? m : m.toFixed(1)}M ctx`
  }
  if (tokens >= 1_000) return `${Math.round(tokens / 1_000)}K ctx`
  return `${tokens} ctx`
}

function formatUsd(n: number): string {
  if (n >= 1) return `$${Number.isInteger(n) ? n : n.toFixed(2)}`
  return `$${n.toFixed(2)}`
}

function formatPrice(opt: ModelOption): string | null {
  const { input_cost_per_1m: i, output_cost_per_1m: o } = opt
  // The API sends null for fields a model has no value for.
  if (i == null && o == null) return null
  if (i != null && o != null)
    return `${formatUsd(i)} / ${formatUsd(o)} per 1M`
  return `${formatUsd((i ?? o) as number)} per 1M`
}

function Chip({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className="shrink-0 rounded border px-1 py-px text-[10px] leading-4 text-muted-foreground"
    >
      {children}
    </span>
  )
}

// Compact metadata chips (context window, price, reasoning) for an option.
// Renders nothing when the backend supplied no metadata.
function OptionChips({ opt }: { opt: ModelOption }) {
  const price = formatPrice(opt)
  if (!opt.context_window && !price && !opt.reasoning) return null
  return (
    <span className="flex flex-wrap items-center gap-1">
      {opt.context_window ? (
        <Chip
          title={`${opt.context_window.toLocaleString()} token context window`}
        >
          {formatContext(opt.context_window)}
        </Chip>
      ) : null}
      {price && (
        <Chip title="Input / output price in USD per 1M tokens">{price}</Chip>
      )}
      {opt.reasoning && (
        <Chip title="Supports extended reasoning">Reasoning</Chip>
      )}
    </span>
  )
}

function ComboboxItem({
  onPick,
  highlighted,
  onHover,
  children,
}: {
  onPick: () => void
  highlighted: boolean
  onHover: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={highlighted}
      ref={(el) => {
        if (highlighted) el?.scrollIntoView({ block: "nearest" })
      }}
      className={`flex w-full min-w-0 flex-col items-stretch gap-0.5 px-2 py-1.5 text-left text-sm ${
        highlighted ? "bg-accent text-accent-foreground" : ""
      }`}
      onMouseDown={(e) => {
        e.preventDefault()
        onPick()
      }}
      onMouseEnter={onHover}
    >
      {children}
    </button>
  )
}

// Searchable model picker. Typing filters the backend's catalog; arrows +
// Enter or click select; free-form ids commit via the "Use …" row. When
// `configModel` is set, an "Inherit from deployment config" row is pinned
// first and selecting it yields value "".
export function ModelCombobox({
  value,
  onChange,
  options,
  configModel,
}: {
  value: string
  onChange: (v: string) => void
  options: ModelOption[]
  configModel?: string
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [highlight, setHighlight] = useState(-1)

  const selected =
    value === "" && configModel !== undefined
      ? `Inherit from deployment config (${configModel})`
      : (options.find((o) => o.value === value)?.label ?? value)
  const q = query.trim().toLowerCase()
  const filtered = q
    ? options.filter((o) =>
        `${o.label} ${o.value} ${o.description ?? ""}`.toLowerCase().includes(q)
      )
    : options
  const showInherit = configModel !== undefined
  const custom =
    query.trim() && !filtered.some((o) => o.value === query.trim())
      ? query.trim()
      : null
  const rowCount = (showInherit ? 1 : 0) + filtered.length + (custom ? 1 : 0)

  const pick = (v: string) => {
    onChange(v)
    inputRef.current?.blur()
  }

  const pickAt = (i: number) => {
    if (showInherit && i === 0) return pick("")
    const j = i - (showInherit ? 1 : 0)
    if (j < filtered.length) return pick(filtered[j].value)
    if (custom) return pick(custom)
  }

  // Row index of the first catalog option (after the inherit row, if any).
  const firstOption = showInherit ? 1 : 0

  return (
    <div className="relative">
      <Input
        ref={inputRef}
        role="combobox"
        aria-expanded={open}
        value={open ? query : selected}
        placeholder="Search models…"
        onFocus={() => {
          setOpen(true)
          setQuery("")
          setHighlight(-1)
        }}
        onBlur={() => setOpen(false)}
        onChange={(e) => {
          setQuery(e.target.value)
          setOpen(true)
          setHighlight(-1)
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault()
            setHighlight((h) => Math.min(h + 1, rowCount - 1))
          } else if (e.key === "ArrowUp") {
            e.preventDefault()
            setHighlight((h) => Math.max(h - 1, 0))
          } else if (e.key === "Enter") {
            if (highlight >= 0) pickAt(highlight)
            else if (query.trim())
              // Prefer the top visible match; raw text only when nothing matches.
              pickAt(filtered.length > 0 ? firstOption : rowCount - 1)
          } else if (e.key === "Escape") {
            inputRef.current?.blur()
          }
        }}
      />
      {open && (
        <div
          role="listbox"
          className="absolute z-50 mt-1 max-h-64 w-full overflow-y-auto rounded-md border bg-popover text-popover-foreground shadow-md"
          onMouseDown={(e) => e.preventDefault()}
        >
          {showInherit && (
            <ComboboxItem
              onPick={() => pick("")}
              highlighted={highlight === 0}
              onHover={() => setHighlight(0)}
            >
              <span className="flex min-w-0 items-center">
                Inherit from deployment config
                <span className="ml-2 truncate font-mono text-xs text-muted-foreground">
                  {configModel}
                </span>
              </span>
            </ComboboxItem>
          )}
          {filtered.map((opt, i) => (
            <ComboboxItem
              key={opt.value}
              onPick={() => pick(opt.value)}
              highlighted={highlight === firstOption + i}
              onHover={() => setHighlight(firstOption + i)}
            >
              <span className="flex min-w-0 items-center">
                <span className="truncate">{opt.label}</span>
                {opt.value !== opt.label && (
                  <span className="ml-2 truncate font-mono text-xs text-muted-foreground">
                    {opt.value}
                  </span>
                )}
                {opt.recommended && (
                  <span className="ml-auto shrink-0 pl-2 text-xs text-muted-foreground">
                    Recommended
                  </span>
                )}
              </span>
              {opt.description && (
                <span className="truncate text-xs text-muted-foreground">
                  {opt.description}
                </span>
              )}
              <OptionChips opt={opt} />
            </ComboboxItem>
          ))}
          {custom && (
            <ComboboxItem
              onPick={() => pick(custom)}
              highlighted={highlight === rowCount - 1}
              onHover={() => setHighlight(rowCount - 1)}
            >
              Use “{custom}”
            </ComboboxItem>
          )}
        </div>
      )}
    </div>
  )
}
