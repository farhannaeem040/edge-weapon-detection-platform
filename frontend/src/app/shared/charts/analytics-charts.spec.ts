import { ComponentFixture, TestBed } from '@angular/core/testing';

import { AnalyticsAreaChartComponent } from './analytics-area-chart';
import { AnalyticsBarChartComponent } from './analytics-bar-chart';
import { AnalyticsDonutChartComponent } from './analytics-donut-chart';
import { ChartPoint, axisTicks, labelIndexes, niceAxisMaximum } from './chart.models';

/**
 * FS-15 §7 / IP-17 T-13/T-17. The dependency-free analytics charts.
 *
 * The behaviours asserted here are the ones that carry meaning rather than decoration: an unmeasured
 * bucket must render as a gap and never as zero, an empty series must render its empty state and
 * never an axis implying zeros, and every chart must remain drawable at 0, 1, and many data points.
 */
describe('chart.models', () => {
  it('rounds an axis maximum up to a readable 1/2/5 × 10ⁿ value', () => {
    expect(niceAxisMaximum(7)).toBe(10);
    expect(niceAxisMaximum(12)).toBe(20);
    expect(niceAxisMaximum(41)).toBe(50);
    expect(niceAxisMaximum(151)).toBe(200);
    expect(niceAxisMaximum(1)).toBe(1);
  });

  it('never returns a zero maximum, so an all-zero series still has a usable scale', () => {
    expect(niceAxisMaximum(0)).toBe(1);
    expect(niceAxisMaximum(-5)).toBe(1);
    expect(niceAxisMaximum(Number.NaN)).toBe(1);
  });

  it('produces evenly spaced ticks from zero to the maximum inclusive', () => {
    expect(axisTicks(100, 4)).toEqual([0, 25, 50, 75, 100]);
  });

  it('keeps every label when the series is short', () => {
    expect(labelIndexes(5)).toEqual([0, 1, 2, 3, 4]);
  });

  it('samples labels, always keeping the first and last, when the series is long', () => {
    const indexes = labelIndexes(90);

    expect(indexes.length).toBeLessThanOrEqual(7);
    expect(indexes[0]).toBe(0);
    expect(indexes[indexes.length - 1]).toBe(89);
  });
});

describe('AnalyticsAreaChartComponent', () => {
  let fixture: ComponentFixture<AnalyticsAreaChartComponent>;

  function render(points: ChartPoint[], filled = true): HTMLElement {
    fixture = TestBed.createComponent(AnalyticsAreaChartComponent);
    fixture.componentRef.setInput('points', points);
    fixture.componentRef.setInput('filled', filled);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  function point(label: string, value: number | null): ChartPoint {
    return { label, value, tooltip: value === null ? 'No samples' : `${value} detections` };
  }

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [AnalyticsAreaChartComponent] }).compileComponents();
  });

  it('renders an empty state rather than an axis when there are no points', () => {
    const element = render([]);

    expect(element.querySelector('svg')).toBeNull();
    expect(element.textContent).toContain('No data for the selected filters.');
  });

  it('renders an empty state when every bucket is unmeasured', () => {
    // All-null is "nothing was measured", which must not be drawn as a flat line along zero.
    const element = render([point('1 Aug', null), point('2 Aug', null)]);

    expect(element.querySelector('svg')).toBeNull();
  });

  it('draws a chart for a measured series', () => {
    const element = render([point('1 Aug', 3), point('2 Aug', 7), point('3 Aug', 5)]);

    expect(element.querySelector('svg')).not.toBeNull();
    expect(element.querySelectorAll('.chart__line').length).toBe(1);
  });

  it('splits the line into separate paths around an unmeasured bucket, leaving a gap', () => {
    // The core honesty rule: a null must not pull the line down to the baseline (FS-15 §5.4).
    const element = render([point('1 Aug', 3), point('2 Aug', null), point('3 Aug', 5)]);

    expect(element.querySelectorAll('.chart__line').length).toBe(2);
  });

  it('draws a measured zero as a real point on the baseline, not as a gap', () => {
    const element = render([point('1 Aug', 0), point('2 Aug', 0), point('3 Aug', 0)]);

    expect(element.querySelectorAll('.chart__line').length).toBe(1);
    expect(element.querySelectorAll('.chart__marker').length).toBe(3);
  });

  it('omits the area fill when the series has a gap, so the gap is never quietly filled in', () => {
    const element = render([point('1 Aug', 3), point('2 Aug', null), point('3 Aug', 5)]);

    expect(element.querySelector('.chart__area')).toBeNull();
  });

  it('fills the area for a fully measured series in area mode', () => {
    const element = render([point('1 Aug', 3), point('2 Aug', 7)]);

    expect(element.querySelector('.chart__area')).not.toBeNull();
  });

  it('draws no area fill in line mode', () => {
    const element = render([point('1 Aug', 3), point('2 Aug', 7)], false);

    expect(element.querySelector('.chart__area')).toBeNull();
    expect(element.querySelector('.chart__line--thin')).not.toBeNull();
  });

  it('gives every measured point a tooltip carrying its label and value', () => {
    const element = render([point('1 Aug', 3), point('2 Aug', 7)]);
    const titles = Array.from(element.querySelectorAll('.chart__marker title')).map(
      (node) => node.textContent ?? '',
    );

    expect(titles).toEqual(['1 Aug: 3 detections', '2 Aug: 7 detections']);
  });

  it('renders no marker for an unmeasured bucket', () => {
    const element = render([point('1 Aug', 3), point('2 Aug', null)]);

    expect(element.querySelectorAll('.chart__marker').length).toBe(1);
  });

  it('renders a y-axis of round tick values', () => {
    const element = render([point('1 Aug', 41)]);
    const ticks = Array.from(element.querySelectorAll('.chart__y-tick')).map(
      (node) => node.textContent ?? '',
    );

    expect(ticks).toEqual(['0', '12.5', '25', '37.5', '50']);
  });

  it('samples x-axis labels so a long series does not overprint them', () => {
    const points = Array.from({ length: 90 }, (_, index) => point(`d${index}`, index));
    const element = render(points);

    expect(element.querySelectorAll('.chart__x-tick').length).toBeLessThanOrEqual(7);
  });

  it('scales to its container rather than to a fixed pixel width', () => {
    const svg = render([point('1 Aug', 3), point('2 Aug', 7)]).querySelector('svg')!;

    expect(svg.getAttribute('preserveAspectRatio')).toBe('none');
    expect(svg.getAttribute('viewBox')).toBe('0 0 1000 300');
  });

  it('exposes a text equivalent of the series for assistive technology', () => {
    const element = render([point('1 Aug', 3), point('3 Aug', 7)]);

    expect(element.querySelector('figcaption')!.textContent).toContain('1 Aug');
    expect(element.querySelector('svg')!.getAttribute('aria-label')).toContain('7 detections');
  });

  it('renders a single measured point without failing', () => {
    const element = render([point('1 Aug', 5)]);

    expect(element.querySelectorAll('.chart__marker').length).toBe(1);
  });
});

describe('AnalyticsBarChartComponent', () => {
  let fixture: ComponentFixture<AnalyticsBarChartComponent>;

  function render(data: { label: string; value: number; tooltip: string }[]): HTMLElement {
    fixture = TestBed.createComponent(AnalyticsBarChartComponent);
    fixture.componentRef.setInput('data', data);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  function bar(label: string, value: number) {
    return { label, value, tooltip: `${label}: ${value} alerts` };
  }

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [AnalyticsBarChartComponent] }).compileComponents();
  });

  it('renders an empty state when there are no Branches', () => {
    const element = render([]);

    expect(element.querySelector('.bars__list')).toBeNull();
    expect(element.textContent).toContain('No data for the selected filters.');
  });

  it('renders a single Branch without stretching it across the card', () => {
    const element = render([bar('Ljmu Branch', 12)]);
    const fill = element.querySelector('.bars__fill') as HTMLElement;

    expect(element.querySelectorAll('.bars__item').length).toBe(1);
    // Stitch barThickness: a lone bar keeps its width instead of filling the plot.
    expect(getComputedStyle(fill).width).toBe('28px');
  });

  it('renders many Branches without dropping any', () => {
    const element = render(Array.from({ length: 12 }, (_, index) => bar(`Branch ${index}`, index)));

    expect(element.querySelectorAll('.bars__item').length).toBe(12);
  });

  it('renders a quiet Branch with a visible zero bar and its real name', () => {
    // "Quiet" must stay distinguishable from "missing" (FS-15 §5.3).
    const element = render([bar('Busy', 10), bar('Quiet', 0)]);
    const zeroBar = element.querySelectorAll('.bars__fill')[1] as HTMLElement;

    expect(zeroBar.classList.contains('bars__fill--zero')).toBeTrue();
    expect(element.textContent).toContain('Quiet');
  });

  it('uses real Branch names from the data, never a mockup placeholder', () => {
    const element = render([bar('Ljmu Branch', 594)]);

    expect(element.textContent).toContain('Ljmu Branch');
    expect(element.textContent).not.toContain('Logistics Hub');
  });

  it('sizes each bar proportionally to the axis maximum', () => {
    const element = render([bar('A', 50), bar('B', 25)]);
    const fills = Array.from(element.querySelectorAll('.bars__fill')) as HTMLElement[];

    // niceAxisMaximum(50) === 50, so the bars are 100 % and 50 % of the plot height.
    expect(fills[0].style.height).toBe('100%');
    expect(fills[1].style.height).toBe('50%');
  });

  it('gives each bar a tooltip', () => {
    const element = render([bar('Ljmu Branch', 3)]);

    expect((element.querySelector('.bars__column') as HTMLElement).title).toBe(
      'Ljmu Branch: 3 alerts',
    );
  });

  it('exposes a text equivalent of the bars for assistive technology', () => {
    const element = render([bar('Ljmu Branch', 3)]);

    expect(element.querySelector('figcaption')!.textContent).toContain('Ljmu Branch 3');
  });
});

describe('AnalyticsDonutChartComponent', () => {
  let fixture: ComponentFixture<AnalyticsDonutChartComponent>;

  function render(
    slices: { label: string; value: number; color: string }[],
    centreValue = '80.0%',
  ): HTMLElement {
    fixture = TestBed.createComponent(AnalyticsDonutChartComponent);
    fixture.componentRef.setInput('slices', slices);
    fixture.componentRef.setInput('centreValue', centreValue);
    fixture.componentRef.setInput('centreCaption', 'Mean confidence');
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  const slices = [
    { label: 'High (≥ 75%)', value: 3, color: 'var(--color-primary)' },
    { label: 'Medium (50–75%)', value: 1, color: 'var(--color-secondary)' },
    { label: 'Low (< 50%)', value: 0, color: 'var(--color-border-strong)' },
  ];

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AnalyticsDonutChartComponent],
    }).compileComponents();
  });

  it('renders one arc and one legend row per slice', () => {
    const element = render(slices);

    expect(element.querySelectorAll('.donut__arc').length).toBe(3);
    expect(element.querySelectorAll('.donut__legend-row').length).toBe(3);
  });

  it('renders the centre value and caption exactly as supplied', () => {
    const element = render(slices);

    expect(element.querySelector('.donut__value')!.textContent).toBe('80.0%');
    expect(element.querySelector('.donut__caption')!.textContent).toBe('Mean confidence');
  });

  it('renders an em dash rather than a percentage when the metric is unavailable', () => {
    // Never "0%": no detections means nothing was measured, not that confidence was zero.
    const element = render(
      slices.map((slice) => ({ ...slice, value: 0 })),
      '—',
    );

    expect(element.querySelector('.donut__value')!.textContent).toBe('—');
  });

  it('draws no filled arc at all when every slice is zero', () => {
    const element = render(
      slices.map((slice) => ({ ...slice, value: 0 })),
      '—',
    );
    const arcs = Array.from(element.querySelectorAll('.donut__arc')) as SVGCircleElement[];

    expect(arcs.every((arc) => arc.getAttribute('stroke-dasharray')!.startsWith('0 '))).toBeTrue();
  });

  it('gives a single non-zero slice the whole ring', () => {
    const element = render([{ label: 'High', value: 5, color: 'var(--color-primary)' }]);
    const arc = element.querySelector('.donut__arc') as SVGCircleElement;
    const [drawn] = arc.getAttribute('stroke-dasharray')!.split(' ').map(Number);

    expect(drawn).toBeCloseTo(2 * Math.PI * 92, 1);
  });

  it('lays the arcs end to end rather than overlapping them', () => {
    const element = render([
      { label: 'A', value: 1, color: 'a' },
      { label: 'B', value: 1, color: 'b' },
    ]);
    const arcs = Array.from(element.querySelectorAll('.donut__arc')) as SVGCircleElement[];
    const circumference = 2 * Math.PI * 92;

    expect(Number(arcs[0].getAttribute('stroke-dashoffset'))).toBeCloseTo(0, 1);
    expect(Number(arcs[1].getAttribute('stroke-dashoffset'))).toBeCloseTo(-circumference / 2, 1);
  });

  it('shows each slice value in the legend', () => {
    const element = render(slices);
    const values = Array.from(element.querySelectorAll('.donut__legend-value')).map(
      (node) => node.textContent,
    );

    expect(values).toEqual(['3', '1', '0']);
  });

  it('exposes a text equivalent for assistive technology', () => {
    const element = render(slices);

    expect(element.querySelector('figcaption')!.textContent).toContain('Mean confidence: 80.0%');
  });
});
