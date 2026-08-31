import type {
  ButtonHTMLAttributes,
  HTMLAttributes,
  InputHTMLAttributes,
  KeyboardEvent as ReactKeyboardEvent,
  ReactNode,
  TextareaHTMLAttributes,
} from 'react'
import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown } from 'lucide-react'

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
  title,
  onClick,
}: {
  tone?: StatusTone
  children: ReactNode
  pulse?: boolean
  className?: string
  title?: string
  /** 상태가 곧 할 일일 때만 — 누르면 그걸 고칠 화면으로 간다 */
  onClick?: () => void
}) {
  const content = (
    <>
      <span aria-hidden="true" className="ui-status__glyph">{statusGlyph[tone]}</span>
      <span>{children}</span>
    </>
  )
  const classes = cx('ui-status', `ui-status--${tone}`, pulse && 'ui-status--pulse', className)
  if (onClick) {
    return <button type="button" title={title} className={classes} onClick={onClick}>{content}</button>
  }
  return <span title={title} className={classes}>{content}</span>
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

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cx('ui-input', 'ui-textarea', className)} {...props} />
}

export type ListboxOption<T extends string> = {
  value: T
  label: string
  /** 라벨 오른쪽 보조 텍스트 — 프로바이더 구분 등 */
  hint?: string
  disabled?: boolean
}

/** 네이티브 select 대신 쓰는 목록 상자.
 *
 *  네이티브 select는 트리거를 아무리 스타일해도 열린 목록이 OS 메뉴로 그려져
 *  앱 UI와 어긋난다. 목록을 body 포털로 띄우는 이유는 `.janus-composer`처럼
 *  `overflow: hidden`인 조상이 in-flow 팝업을 잘라내기 때문이다.
 *
 *  ARIA 1.2 select-only combobox 패턴 — 포커스는 트리거에 남고 활성 옵션은
 *  aria-activedescendant로 가리킨다.
 */
export function Listbox<T extends string>({
  value,
  options,
  onChange,
  label,
  placeholder,
  placement = 'auto',
  compact = false,
  disabled = false,
  className,
  listClassName,
}: {
  value: T
  options: readonly ListboxOption<T>[]
  onChange: (value: T) => void
  label: string
  /** 아직 아무것도 고르지 않았을 때 트리거에 보일 문구 */
  placeholder?: string
  placement?: 'auto' | 'top' | 'bottom'
  compact?: boolean
  disabled?: boolean
  className?: string
  listClassName?: string
}) {
  const id = useId()
  const trigger = useRef<HTMLButtonElement>(null)
  const [open, setOpen] = useState(false)
  const [cursor, setCursor] = useState(0)
  const [box, setBox] = useState<{ left: number; top?: number; bottom?: number; width: number } | null>(null)
  const selectedIndex = options.findIndex((option) => option.value === value)
  const selected = options[selectedIndex]

  const openList = () => {
    const rect = trigger.current?.getBoundingClientRect()
    if (disabled || options.length === 0 || !rect) return
    // 컴포저처럼 화면 아래쪽에 붙은 트리거는 위로 열려야 목록이 잘리지 않는다.
    const below = window.innerHeight - rect.bottom
    const dropUp = placement === 'top' || (placement === 'auto' && below < 200 && rect.top > below)
    setBox({
      left: rect.left,
      width: rect.width,
      ...(dropUp ? { bottom: window.innerHeight - rect.top + 6 } : { top: rect.bottom + 6 }),
    })
    setCursor(selectedIndex < 0 ? 0 : selectedIndex)
    setOpen(true)
  }

  const close = () => {
    setOpen(false)
    trigger.current?.focus()
  }

  const commit = (index: number) => {
    const option = options[index]
    if (!option || option.disabled) return
    if (option.value !== value) onChange(option.value)
    close()
  }

  useEffect(() => {
    if (!open) return
    const dismiss = (event: Event) => {
      const target = event.target as HTMLElement | null
      if (trigger.current?.contains(target as Node)) return  // 토글은 onClick이 처리
      if (target?.closest?.(`[data-listbox="${id}"]`)) return
      setOpen(false)
    }
    // 위치를 열 때 한 번만 재므로, 스크롤·리사이즈되면 떠 있는 채로 어긋난다.
    const dismissOnMove = () => setOpen(false)
    document.addEventListener('pointerdown', dismiss)
    window.addEventListener('resize', dismissOnMove)
    window.addEventListener('scroll', dismissOnMove, true)
    return () => {
      document.removeEventListener('pointerdown', dismiss)
      window.removeEventListener('resize', dismissOnMove)
      window.removeEventListener('scroll', dismissOnMove, true)
    }
  }, [open, id])

  const move = (delta: number) => setCursor((index) => {
    let next = index
    for (let step = 0; step < options.length; step += 1) {
      next = (next + delta + options.length) % options.length
      if (!options[next].disabled) return next
    }
    return index
  })

  const onKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (!open) {
      if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) {
        event.preventDefault()
        openList()
      }
      return
    }
    if (event.key === 'Escape') { event.preventDefault(); close() }
    else if (event.key === 'ArrowDown') { event.preventDefault(); move(1) }
    else if (event.key === 'ArrowUp') { event.preventDefault(); move(-1) }
    else if (event.key === 'Home') { event.preventDefault(); setCursor(0) }
    else if (event.key === 'End') { event.preventDefault(); setCursor(options.length - 1) }
    else if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); commit(cursor) }
    else if (event.key === 'Tab') setOpen(false)
  }

  return (
    <>
      <button
        ref={trigger}
        type="button"
        disabled={disabled}
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? `${id}-list` : undefined}
        aria-activedescendant={open ? `${id}-option-${cursor}` : undefined}
        aria-label={label}
        className={cx('ui-listbox', compact && 'ui-listbox--compact', className)}
        onClick={() => (open ? setOpen(false) : openList())}
        onKeyDown={onKeyDown}
      >
        <span className={cx('ui-listbox__value', !selected && 'ui-listbox__value--placeholder')}>
          {selected?.label ?? placeholder ?? ''}
        </span>
        <ChevronDown size={compact ? 11 : 13} strokeWidth={1.75} aria-hidden="true" />
      </button>
      {open && box && createPortal(
        <div
          id={`${id}-list`}
          data-listbox={id}
          role="listbox"
          aria-label={label}
          className={cx('ui-listbox__list', listClassName)}
          style={{ left: box.left, top: box.top, bottom: box.bottom, minWidth: box.width }}
        >
          {options.map((option, index) => (
            <button
              key={option.value}
              id={`${id}-option-${index}`}
              type="button"
              role="option"
              aria-selected={option.value === value}
              disabled={option.disabled}
              data-active={index === cursor ? '' : undefined}
              className="ui-listbox__option"
              onMouseEnter={() => setCursor(index)}
              onClick={() => commit(index)}
            >
              <Check size={12} strokeWidth={2.25} aria-hidden="true" />
              <span>{option.label}</span>
              {option.hint && <small>{option.hint}</small>}
            </button>
          ))}
        </div>,
        document.body,
      )}
    </>
  )
}

export type MenuColumn<T extends string = string> = {
  /** 컬럼의 접근성 이름. 테스트와 스크린리더가 이걸로 컬럼을 찾는다. */
  label: string
  value: T
  options: readonly ListboxOption<T>[]
  onChange: (value: T) => void
}

/** 단계가 있는 선택을 칩 하나로 접는다 — 컬럼이 왼쪽에서 오른쪽으로 이어진다.
 *
 *  진짜 flyout 대신 한 패널 안의 컬럼들로 그린다: 띄울 상자가 하나뿐이라
 *  위치 계산이 한 번이면 끝나고, 컬럼이 몇 개든 화면 밖으로 새지 않는다.
 *  각 컬럼은 앞 컬럼의 **확정된** 선택을 따른다 — 마우스가 스쳐 간 값이 아니라.
 *  마지막 컬럼에서 고르면 닫히고, 앞 컬럼에서 고르면 열린 채 뒤가 갱신된다.
 */
export function CascadingMenu({
  label,
  summary,
  columns,
  placement = 'auto',
  compact = false,
  disabled = false,
  className,
  icon,
}: {
  label: string
  summary: string
  columns: readonly MenuColumn[]
  placement?: 'auto' | 'top' | 'bottom'
  compact?: boolean
  disabled?: boolean
  className?: string
  icon?: ReactNode
}) {
  const id = useId()
  const trigger = useRef<HTMLButtonElement>(null)
  const [open, setOpen] = useState(false)
  const [box, setBox] = useState<{ left: number; top?: number; bottom?: number } | null>(null)
  const [cursor, setCursor] = useState<[number, number]>([0, 0])
  const [pending, setPending] = useState<number | null>(null)

  const indexOf = (column: number) => {
    const found = columns[column]?.options.findIndex(
      (option) => option.value === columns[column].value,
    )
    return found === undefined || found < 0 ? 0 : found
  }

  const openMenu = () => {
    const rect = trigger.current?.getBoundingClientRect()
    if (disabled || columns.length === 0 || !rect) return
    const below = window.innerHeight - rect.bottom
    const dropUp = placement === 'top' || (placement === 'auto' && below < 220 && rect.top > below)
    setBox({
      left: rect.left,
      ...(dropUp ? { bottom: window.innerHeight - rect.top + 6 } : { top: rect.bottom + 6 }),
    })
    setCursor([0, indexOf(0)])
    setOpen(true)
  }

  const close = () => {
    setOpen(false)
    trigger.current?.focus()
  }

  const commit = (column: number, index: number) => {
    const option = columns[column]?.options[index]
    if (!option || option.disabled) return
    if (option.value !== columns[column].value) columns[column].onChange(option.value)
    setCursor([column, index])
    // 닫을지는 **갱신된 뒤에** 정한다. 고른 값이 하위 단계를 새로 여는 경우
    // (로컬 → 구독형: 컬럼 1개 → 3개) 클릭 시점의 컬럼 수로 판단하면 방금
    // 펼쳐진 단계를 보여주지도 않고 닫힌다.
    setPending(column)
  }

  useEffect(() => {
    if (pending === null) return
    setPending(null)
    if (pending >= columns.length - 1) close()
    // close는 트리거로 포커스를 되돌릴 뿐 렌더에 쓰는 값을 읽지 않는다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending, columns.length])

  useEffect(() => {
    if (!open) return
    const dismiss = (event: Event) => {
      const target = event.target as HTMLElement | null
      if (trigger.current?.contains(target as Node)) return
      if (target?.closest?.(`[data-cascade="${id}"]`)) return
      setOpen(false)
    }
    const dismissOnMove = () => setOpen(false)
    document.addEventListener('pointerdown', dismiss)
    window.addEventListener('resize', dismissOnMove)
    window.addEventListener('scroll', dismissOnMove, true)
    return () => {
      document.removeEventListener('pointerdown', dismiss)
      window.removeEventListener('resize', dismissOnMove)
      window.removeEventListener('scroll', dismissOnMove, true)
    }
  }, [open, id])

  const onKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (!open) {
      if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) {
        event.preventDefault()
        openMenu()
      }
      return
    }
    const [column, index] = cursor
    const options = columns[column]?.options ?? []
    const move = (delta: number) => {
      let next = index
      for (let step = 0; step < options.length; step += 1) {
        next = (next + delta + options.length) % options.length
        if (!options[next].disabled) return setCursor([column, next])
      }
    }
    if (event.key === 'Escape') { event.preventDefault(); close() }
    else if (event.key === 'ArrowDown') { event.preventDefault(); move(1) }
    else if (event.key === 'ArrowUp') { event.preventDefault(); move(-1) }
    else if (event.key === 'ArrowRight') {
      event.preventDefault()
      if (column + 1 < columns.length) setCursor([column + 1, indexOf(column + 1)])
    } else if (event.key === 'ArrowLeft') {
      event.preventDefault()
      if (column > 0) setCursor([column - 1, indexOf(column - 1)])
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      commit(column, index)
    } else if (event.key === 'Tab') setOpen(false)
  }

  return (
    <>
      <button
        ref={trigger}
        type="button"
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        className={cx('ui-listbox', compact && 'ui-listbox--compact', className)}
        onClick={() => (open ? setOpen(false) : openMenu())}
        onKeyDown={onKeyDown}
      >
        {icon}
        <span className="ui-listbox__value">{summary}</span>
        <ChevronDown size={compact ? 11 : 13} strokeWidth={1.75} aria-hidden="true" />
      </button>
      {open && box && createPortal(
        <div
          data-cascade={id}
          className="ui-cascade"
          style={{ left: box.left, top: box.top, bottom: box.bottom }}
        >
          {columns.map((column, columnIndex) => (
            <div
              key={column.label}
              role="listbox"
              aria-label={column.label}
              className="ui-cascade__column"
            >
              {column.options.map((option, index) => (
                <button
                  key={option.value}
                  type="button"
                  role="option"
                  aria-selected={option.value === column.value}
                  disabled={option.disabled}
                  data-active={
                    columnIndex === cursor[0] && index === cursor[1] ? '' : undefined
                  }
                  className="ui-listbox__option"
                  onMouseEnter={() => setCursor([columnIndex, index])}
                  onClick={() => commit(columnIndex, index)}
                >
                  <Check size={12} strokeWidth={2.25} aria-hidden="true" />
                  <span>{option.label}</span>
                  {option.hint && <small>{option.hint}</small>}
                </button>
              ))}
            </div>
          ))}
        </div>,
        document.body,
      )}
    </>
  )
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
    <span className="janus-brand" aria-label="Janus">
      <span className="janus-symbol" aria-hidden="true" />
      {!compact && <span className="janus-wordmark">Janus</span>}
    </span>
  )
}
