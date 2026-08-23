import type {
  ButtonHTMLAttributes,
  HTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from 'react'
import { useEffect, useId, useRef } from 'react'

export function cx(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(' ')
}

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'

export function Button({
  variant = 'secondary',
  compact = false,
  className,
  type = 'button',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant
  compact?: boolean
}) {
  return (
    <button
      type={type}
      className={cx('ui-button', `ui-button--${variant}`, compact && 'ui-button--compact', className)}
      {...props}
    />
  )
}

export function IconButton({
  label,
  className,
  type = 'button',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      type={type}
      aria-label={label}
      title={props.title ?? label}
      className={cx('ui-icon-button', className)}
      {...props}
    />
  )
}

export function Tabs<T extends string>({
  items,
  value,
  onChange,
  label,
  labels,
  className,
}: {
  items: readonly T[]
  value: T
  onChange: (value: T) => void
  label: string
  labels?: Partial<Record<T, ReactNode>>
  className?: string
}) {
  const buttons = useRef<Array<HTMLButtonElement | null>>([])
  const move = (index: number) => {
    const next = (index + items.length) % items.length
    onChange(items[next])
    buttons.current[next]?.focus()
  }
  return (
    <div className={cx('ui-tabs', className)} role="tablist" aria-label={label}>
      {items.map((item, index) => (
        <button
          key={item}
          type="button"
          role="tab"
          aria-selected={item === value}
          tabIndex={item === value ? 0 : -1}
          ref={(node) => { buttons.current[index] = node }}
          className="ui-tab"
          onClick={() => onChange(item)}
          onKeyDown={(event) => {
            if (event.key === 'ArrowRight') { event.preventDefault(); move(index + 1) }
            if (event.key === 'ArrowLeft') { event.preventDefault(); move(index - 1) }
            if (event.key === 'Home') { event.preventDefault(); move(0) }
            if (event.key === 'End') { event.preventDefault(); move(items.length - 1) }
          }}
        >
          {labels?.[item] ?? item}
        </button>
      ))}
    </div>
  )
}

export type StatusTone = 'success' | 'warning' | 'danger' | 'info' | 'muted'

const statusGlyph: Record<StatusTone, string> = {
  success: '●',
  warning: '△',
  danger: '×',
  info: '◉',
  muted: '○',
}

export function Status({
  tone = 'muted',
  children,
  pulse = false,
  className,
}: {
  tone?: StatusTone
  children: ReactNode
  pulse?: boolean
  className?: string
}) {
  return (
    <span className={cx('ui-status', `ui-status--${tone}`, pulse && 'ui-status--pulse', className)}>
      <span aria-hidden="true" className="ui-status__glyph">{statusGlyph[tone]}</span>
      <span>{children}</span>
    </span>
  )
}

export function Panel({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return <section className={cx('ui-panel', className)} {...props} />
}

export function Toolbar({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div role="toolbar" className={cx('ui-toolbar', className)} {...props} />
}

export function Menu({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div role="menu" className={cx('ui-menu', className)} {...props} />
}

export function MenuItem({ className, type = 'button', ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button role="menuitem" type={type} className={cx('ui-menu__item', className)} {...props} />
}

export function Section({
  label,
  description,
  className,
  children,
}: HTMLAttributes<HTMLElement> & { label: string; description?: string }) {
  return (
    <section className={cx('ui-section', className)}>
      <header className="ui-section__header">
        <h3>{label}</h3>
        {description && <p>{description}</p>}
      </header>
      {children}
    </section>
  )
}

export function Field({
  label,
  help,
  error,
  children,
  className,
}: {
  label: string
  help?: string
  error?: string | null
  children: ReactNode
  className?: string
}) {
  return (
    <label className={cx('ui-field', className)}>
      <span className="ui-field__label">{label}</span>
      {children}
      {(error || help) && <span className={cx('ui-field__help', error && 'ui-field__help--error')}>{error || help}</span>}
    </label>
  )
}

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cx('ui-input', className)} {...props} />
}

export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cx('ui-input', 'ui-select', className)} {...props} />
}

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cx('ui-input', 'ui-textarea', className)} {...props} />
}

export function Checkbox({
  label,
  description,
  className,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode; description?: ReactNode }) {
  return (
    <label className={cx('ui-checkbox-row', className)}>
      <input type="checkbox" className="ui-checkbox" {...props} />
      <span><span>{label}</span>{description && <small>{description}</small>}</span>
    </label>
  )
}

export function SegmentedControl<T extends string>({
  items,
  value,
  onChange,
  label,
}: {
  items: readonly { value: T; label: string }[]
  value: T
  onChange: (value: T) => void
  label: string
}) {
  return (
    <div className="ui-segmented" role="group" aria-label={label}>
      {items.map((item) => (
        <button
          key={item.value}
          type="button"
          aria-pressed={item.value === value}
          onClick={() => onChange(item.value)}
        >{item.label}</button>
      ))}
    </div>
  )
}

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

export function Dialog({
  open,
  title,
  onClose,
  children,
  className,
}: {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
  className?: string
}) {
  const titleId = useId()
  const panel = useRef<HTMLElement>(null)
  const previousFocus = useRef<HTMLElement | null>(null)
  useEffect(() => {
    if (!open) return
    previousFocus.current = document.activeElement as HTMLElement | null
    requestAnimationFrame(() => panel.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus())
    const handle = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onClose(); return }
      if (event.key !== 'Tab' || !panel.current) return
      const focusable = Array.from(panel.current.querySelectorAll<HTMLElement>(FOCUSABLE))
      if (focusable.length === 0) { event.preventDefault(); panel.current.focus(); return }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', handle)
    return () => {
      document.removeEventListener('keydown', handle)
      previousFocus.current?.focus()
    }
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="ui-dialog-backdrop" onMouseDown={onClose}>
      <section
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={cx('ui-dialog', className)}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <h2 id={titleId} className="sr-only">{title}</h2>
        {children}
      </section>
    </div>
  )
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = '확인',
  danger = false,
  onConfirm,
  onClose,
}: {
  open: boolean
  title: string
  description?: string
  confirmLabel?: string
  danger?: boolean
  onConfirm: () => void
  onClose: () => void
}) {
  return (
    <Dialog open={open} title={title} onClose={onClose} className="ui-confirm-dialog">
      <div className="ui-confirm-dialog__body"><h3>{title}</h3>{description && <p>{description}</p>}</div>
      <footer className="ui-confirm-dialog__actions">
        <Button variant="ghost" onClick={onClose}>취소</Button>
        <Button variant={danger ? 'danger' : 'primary'} onClick={onConfirm}>{confirmLabel}</Button>
      </footer>
    </Dialog>
  )
}

export function EmptyState({
  symbol,
  title,
  description,
  action,
}: {
  symbol?: ReactNode
  title: string
  description?: string
  action?: ReactNode
}) {
  return (
    <div className="ui-empty-state">
      {symbol && <div className="ui-empty-state__symbol">{symbol}</div>}
      <strong>{title}</strong>
      {description && <p>{description}</p>}
      {action}
    </div>
  )
}

export function BrandMark({ compact = false }: { compact?: boolean }) {
  return (
    <span className={cx('janus-brand', compact && 'janus-brand--compact')} aria-label="Janus">
      <span className="janus-symbol" aria-hidden="true" />
      {!compact && <span className="janus-wordmark">Janus</span>}
    </span>
  )
}
