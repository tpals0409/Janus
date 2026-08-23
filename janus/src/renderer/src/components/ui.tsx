import type {
  ButtonHTMLAttributes,
  HTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from 'react'

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
}: {
  items: readonly T[]
  value: T
  onChange: (value: T) => void
  label: string
}) {
  return (
    <div className="ui-tabs" role="tablist" aria-label={label}>
      {items.map((item) => (
        <button
          key={item}
          type="button"
          role="tab"
          aria-selected={item === value}
          className="ui-tab"
          onClick={() => onChange(item)}
        >
          {item}
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
