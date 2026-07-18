import "./founder-trend.css";

export function FounderTrendChart() {
  const points = [[42, 154], [220, 115], [405, 74], [610, 31]];
  const dates = ["Jan ’25", "Jul ’25", "Jan ’26", "Jul ’26"];
  return <div className="trend-chart" role="img" aria-label="Founder Score improved from 52 in January 2025 to 87 in July 2026">
    <svg viewBox="0 0 660 220" preserveAspectRatio="none" aria-hidden="true">
      {[30, 80, 130, 180].map((y, i) => <g key={y}>
        <line className="chart-grid" x1="42" y1={y} x2="640" y2={y}/>
        <text className="chart-y" x="2" y={y + 3}>{[90, 75, 60, 45][i]}</text>
      </g>)}
      <polyline className="trend-line" points={points.map(point => point.join(",")).join(" ")}/>
      {points.map((point, i) => <circle key={i} className="trend-point" cx={point[0]} cy={point[1]} r="5"/>)}
      <g className="event-label"><line x1="220" y1="115" x2="220" y2="141"/><text x="220" y="155" textAnchor="middle">First OSS launch</text></g>
      <g className="event-label"><line x1="405" y1="74" x2="405" y2="102"/><text x="405" y="116" textAnchor="middle">MIT hack win</text></g>
      <g className="event-label"><line x1="610" y1="31" x2="610" y2="59"/><text x="610" y="73" textAnchor="middle">Tessera traction</text></g>
      <text className="score-label" x="624" y="25">87</text>
      {dates.map((date, i) => <text key={date} className="chart-x" x={[42, 242, 442, 640][i]} y="214" textAnchor={i === 0 ? "start" : i === 3 ? "end" : "middle"}>{date}</text>)}
    </svg>
  </div>;
}
