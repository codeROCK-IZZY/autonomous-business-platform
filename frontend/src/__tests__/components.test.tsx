import { render, screen } from "@testing-library/react";
import { OutcomeBadge, RiskBadge, StatusBadge, Timeline, money } from "../components/agent";
import { SourceTag } from "../components/ui";

describe("agent components", () => {
  it("formats money from cents", () => {
    expect(money(123456)).toBe("$1,234.56");
    expect(money(null)).toBe("—");
  });

  it("labels run status and outcome in plain language", () => {
    render(<><StatusBadge status="waiting_approval" /><OutcomeBadge outcome="rejected_over_budget" /><OutcomeBadge outcome={null} /></>);
    expect(screen.getByText("waiting for approval")).toBeInTheDocument();
    expect(screen.getByText("rejected over budget")).toBeInTheDocument();
  });

  it("shows tool risk levels", () => {
    render(<RiskBadge risk="financial" />);
    expect(screen.getByTitle("Risk level: financial")).toHaveTextContent("financial");
  });

  it("distinguishes AI output from rule-based output", () => {
    const { rerender } = render(<SourceTag source="claude:claude-opus-5-5" />);
    expect(screen.getByText("AI · claude-opus-5-5")).toBeInTheDocument();
    rerender(<SourceTag source="rule:offline" />);
    expect(screen.getByText("Rule-based")).toBeInTheDocument();
  });

  it("renders a timeline with one entry per step", () => {
    const { container } = render(<Timeline items={[
      { key: 1, title: "understand", tone: "done" }, { key: 2, title: "approve", tone: "wait", body: "Waiting for approval" },
    ]} />);
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(screen.getByText("Waiting for approval")).toBeInTheDocument();
  });
});
