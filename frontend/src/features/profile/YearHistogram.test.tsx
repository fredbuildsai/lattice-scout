import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { YearHistogram, yearCounts } from './YearHistogram'

const w = (year: number | null, included = true) => ({ year, included })

describe('yearCounts', () => {
  it('counts included works with a year, filling gaps', () => {
    expect(yearCounts([w(2020), w(2020), w(2022), w(2019, false), w(null)])).toEqual([
      [2020, 2],
      [2021, 0],
      [2022, 1],
    ])
  })
  it('keeps only the latest 20 years', () => {
    const c = yearCounts([w(1990), w(2024)])
    expect(c).toHaveLength(20)
    expect(c[0]![0]).toBe(2005)
  })
})

describe('YearHistogram', () => {
  it('is an image with a text summary and a list of real counts', () => {
    render(<YearHistogram works={[w(2020), w(2020), w(2022)]} />)
    expect(
      screen.getByRole('img', {
        name: /3 included publications per year, 2020 to 2022/,
      }),
    ).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Publications per year' })).toHaveTextContent('2020: 22022: 1')
  })
  it('shows an empty state without years', () => {
    render(<YearHistogram works={[w(null), w(2019, false)]} />)
    expect(screen.getByText('No publication years to chart yet.')).toBeInTheDocument()
    expect(screen.queryByRole('img')).toBeNull()
  })
})
