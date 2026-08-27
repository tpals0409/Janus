import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { useStore } from '../store'
import FileTree from './FileTree'

const CHANGE_SET = {
  sections: {
    committed: [{ path: 'src/old.js', status: 'M' }],
    staged: [],
    unstaged: [{ path: 'README.md', status: 'M' }],
    untracked: [{ path: 'src/new.js', status: '?' }]
  }
} as never

describe('FileTree git decorations', () => {
  afterEach(() => {
    useStore.setState({ changeSet: null, tree: {}, projectId: null })
  })

  it('marks changed files and folders so they can be found without opening each one', () => {
    useStore.setState({
      projects: [{ id: 'p1', name: 'P', repo_path: '/tmp/p' }] as never,
      projectId: 'p1',
      changeSet: CHANGE_SET,
      tree: {
        '': [
          { name: 'README.md', type: 'file', size: 10 },
          { name: 'clean.md', type: 'file', size: 10 },
          { name: 'src', type: 'dir', size: null }
        ]
      } as never
    })
    render(<FileTree />)
    expect(screen.getByLabelText('Git 상태 M')).toBeVisible()
    expect(screen.getByLabelText('폴더 내 Git 변경 있음')).toBeVisible()
    expect(screen.getByText('변경 3')).toBeVisible()
    // 변경 없는 파일에는 배지가 없다
    expect(screen.getAllByLabelText(/Git 상태/)).toHaveLength(1)
  })
})
