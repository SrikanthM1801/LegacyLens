import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AdminGate } from "./components/AdminGate";
import { AreaChart } from "./components/AreaChart";
import { Card } from "./components/Card";
import { ConsentMeter } from "./components/ConsentMeter";
import { Dumbbell } from "./components/Dumbbell";
import { HBars } from "./components/HBars";
import { ConsentChip, OutcomeChip } from "./components/StatusChip";

const DATA = [
  { x: "2026-10-14", y: 5 }, { x: "2026-10-15", y: 20 }, { x: "2026-10-16", y: 10 }, { x: "2026-10-17", y: 15 },
];

beforeEach(() => {
  // jsdom has no layout: give the plot a 600px-wide box so pointer positions map to days
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
    left: 0, top: 0, right: 600, bottom: 200, width: 600, height: 200, x: 0, y: 0, toJSON: () => ({}),
  });
});
afterEach(() => vi.restoreAllMocks());

describe("AreaChart", () => {
  it("draws a 2px line, an end dot and compact y ticks", () => {
    const { container } = render(<AreaChart data={DATA} colorVar="--series-1" name="Messages learned" />);
    const line = container.querySelector('path[stroke="var(--series-1)"]')!;
    expect(line.getAttribute("stroke-width")).toBe("2");
    expect(container.querySelectorAll("circle")).toHaveLength(1);
    expect(container.querySelector("circle")!.getAttribute("stroke-width")).toBe("2"); // 2px surface ring
    expect(screen.getByText("20")).toBeInTheDocument(); // 0, 10, 20 ticks
  });

  it("snaps a crosshair + tooltip to the nearest day on hover", () => {
    const { container } = render(<AreaChart data={DATA} colorVar="--series-1" name="Messages learned" />);
    const plot = container.querySelector("rect")!;
    fireEvent.pointerMove(plot, { clientX: 250 }); // x 250/600 of the way across -> second day
    const tip = screen.getByRole("tooltip");
    expect(within(tip).getByText("20")).toBeInTheDocument();
    expect(within(tip).getByText("Messages learned")).toBeInTheDocument();
    expect(container.querySelector(".cross")).toBeInTheDocument();
    fireEvent.pointerLeave(plot);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("is readable from the keyboard, with the same tooltip", async () => {
    const user = userEvent.setup();
    render(<AreaChart data={DATA} colorVar="--series-2" name="Questions" />);
    const svg = screen.getByRole("img");
    expect(svg).toHaveAccessibleName(/Questions per day, 4 days, latest 15/);
    await user.tab();
    expect(within(screen.getByRole("tooltip")).getByText("15")).toBeInTheDocument(); // focus lands on the latest day
    await user.keyboard("{ArrowLeft}");
    expect(within(screen.getByRole("tooltip")).getByText("10")).toBeInTheDocument();
    await user.keyboard("{Home}");
    expect(within(screen.getByRole("tooltip")).getByText("5")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("renders nothing for no data and handles a single day", () => {
    const { container, rerender } = render(<AreaChart data={[]} colorVar="--series-1" name="x" />);
    expect(container.firstChild).toBeNull();
    rerender(<AreaChart data={[{ x: "2026-10-17", y: 3 }]} colorVar="--series-1" name="x" />);
    expect(container.querySelector("path")).toBeInTheDocument();
  });
});

describe("HBars", () => {
  const items = [
    { key: "a", label: "Payments Platform", value: 372, detail: "56 questions" },
    { key: "b", label: "Settlement ops", value: 83 },
  ];

  it("uses one hue, values at the tip, and bars in proportion", () => {
    const { container } = render(<HBars items={items} />);
    const bars = [...container.querySelectorAll<HTMLElement>(".hbar__bar")];
    expect(bars.map((b) => b.style.background)).toEqual(["var(--series-1)", "var(--series-1)"]);
    expect(bars[0].style.width).toMatch(/calc\(1 \* .*110px/);          // the longest bar takes all the room
    expect(bars[1].style.width).toContain(String(83 / 372));            // the others in proportion
    expect(screen.getByText("372")).toBeInTheDocument();
    expect(screen.getByText("56 questions")).toBeInTheDocument();
  });

  it("shows a tooltip on hover and on keyboard focus", async () => {
    const user = userEvent.setup();
    render(<HBars items={items} />);
    fireEvent.pointerMove(screen.getByRole("listitem", { name: "Settlement ops: 83" }), { clientX: 100, clientY: 10 });
    expect(within(screen.getByRole("tooltip")).getByText("Settlement ops")).toBeInTheDocument();
    fireEvent.pointerLeave(screen.getByRole("listitem", { name: "Settlement ops: 83" }));
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    await user.tab();
    expect(within(screen.getByRole("tooltip")).getByText("Payments Platform")).toBeInTheDocument();
  });

  it("explains an empty chart instead of drawing one", () => {
    render(<HBars items={[]} empty="Nothing needed redacting." />);
    expect(screen.getByText("Nothing needed redacting.")).toBeInTheDocument();
    render(<HBars items={[{ key: "z", label: "Zero", value: 0 }]} />);
    expect(screen.getByText("Nothing to show for this period.")).toBeInTheDocument();
  });

  it("treats labels as text, never HTML", () => {
    render(<HBars items={[{ key: "x", label: "<img src=x onerror=alert(1)>", value: 5 }]} />);
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });
});

describe("ConsentMeter", () => {
  it("always lists every status with an icon, a label and a count", () => {
    const { container } = render(<ConsentMeter counts={{ granted: 4, paused: 1, pending: 1, declined: 1 }} />);
    for (const label of ["Allowed", "Paused", "Waiting for a decision", "Not now"]) expect(screen.getByText(label)).toBeInTheDocument();
    expect(container.querySelectorAll(".meter-legend__row svg")).toHaveLength(4);
    expect(screen.getByText("57%")).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAccessibleName("Conversations by consent: 4 allowed, 1 paused, 1 waiting for a decision, 1 not now");
    expect(container.querySelectorAll(".meter__seg")).toHaveLength(4);
  });

  it("omits empty segments but keeps their legend rows", () => {
    const { container } = render(<ConsentMeter counts={{ granted: 2, paused: 0, pending: 0, declined: 0 }} />);
    expect(container.querySelectorAll(".meter__seg")).toHaveLength(1);
    expect(container.querySelectorAll(".meter-legend__row")).toHaveLength(4);
  });
});

describe("Dumbbell", () => {
  it("shows before, after and the drop as text with an arrow", () => {
    const { container } = render(<Dumbbell items={[{ topic: "Payment Gateway", approved_at: "2026-10-13T10:00:00Z", risk_before: 1, risk_after: 0.19 }]} />);
    expect(screen.getByText("−0.81")).toBeInTheDocument();
    expect(container.querySelectorAll(".dumbbell__dot")).toHaveLength(2);
    expect(screen.getByText("Before the interview")).toBeInTheDocument();
    expect(screen.getByText("After approval")).toBeInTheDocument();
    expect(screen.getByLabelText("Payment Gateway: risk 1.00 before, 0.19 after")).toBeInTheDocument();
  });

  it("explains when nothing was approved", () => {
    render(<Dumbbell items={[]} />);
    expect(screen.getByText(/No interviews have been approved/)).toBeInTheDocument();
  });
});

describe("status chips", () => {
  it("never rely on colour alone: icon + label", () => {
    const { container } = render(<><ConsentChip status="paused" /><OutcomeChip outcome="failure" /></>);
    expect(screen.getByText("Paused")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(container.querySelectorAll(".chip svg")).toHaveLength(2);
  });
});

describe("Card table twin", () => {
  it("toggles between the chart and its table", async () => {
    const user = userEvent.setup();
    render(<Card title="T" table={<p>the table</p>}><p>the chart</p></Card>);
    expect(screen.getByText("the chart")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show table" }));
    expect(screen.getByText("the table")).toBeInTheDocument();
    expect(screen.queryByText("the chart")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show chart" })).toHaveAttribute("aria-pressed", "true");
  });
});

describe("AdminGate", () => {
  it("submits the trimmed key and ignores an empty one", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<AdminGate onSubmit={onSubmit} message="That key was not accepted." />);
    expect(screen.getByRole("alert")).toHaveTextContent("not accepted");
    await user.click(screen.getByRole("button", { name: "Open dashboard" }));
    expect(onSubmit).not.toHaveBeenCalled();
    await user.type(screen.getByLabelText("Admin key"), "  s3cret ");
    await user.click(screen.getByRole("button", { name: "Open dashboard" }));
    expect(onSubmit).toHaveBeenCalledWith("s3cret");
  });
});
