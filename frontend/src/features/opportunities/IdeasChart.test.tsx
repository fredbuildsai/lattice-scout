import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { IdeasChart } from './IdeasChart'
import { card } from './fixtures'

describe('IdeasChart', () => {
  it('draws one numbered dot per scored card and lists the values for screen readers', () => {
    const cards = [card('a', 'resubmission'), card('b', 'white_spot', { scores: { fit: 0.5, novelty: 0.2, readiness: 0.9, effort: 0.5 } }), card('c', 'synergy', { scores: null })]
    const { container } = render(<IdeasChart cards={cards} />)
    const svg = screen.getByRole('img')
    expect(svg).toHaveAccessibleName(/Scatter chart of 2 ideas/)
    expect(container.querySelectorAll('circle')).toHaveLength(2) // the unscored card is not plotted
    const list = screen.getByRole('list', { name: 'Chart values' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(2)
    expect(within(list).getByText(/2\. .*novelty 0\.2/)).toBeInTheDocument()
  })

  it('says so when nothing is scored', () => {
    render(<IdeasChart cards={[card('c', 'synergy', { scores: null })]} />)
    expect(screen.getByText('No scored ideas to plot yet.')).toBeInTheDocument()
    expect(screen.queryByRole('img')).toBeNull()
  })
})
