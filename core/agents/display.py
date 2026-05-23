"""Rich console display for orchestrator results."""

from core.agents.models import OrchestratorResult, Signal

# Rich is optional — fall back to plain print if not installed
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    HAS_RICH = True
except ImportError:
    HAS_RICH = False


def display_results(result: OrchestratorResult) -> None:
    if HAS_RICH:
        _display_rich(result)
    else:
        _display_plain(result)


# ── Rich display ──────────────────────────────────────────────────────────

def _display_rich(result: OrchestratorResult) -> None:
    console = Console()

    # Header
    header = Text()
    header.append("Swing Sentinel — Multi-Agent Analysis", style="bold cyan")
    header.append(f"\n{result.timestamp:%Y-%m-%d %H:%M}", style="dim")
    header.append(
        f"\nScanned: {result.scanned_count} | "
        f"Filtered: {result.filtered_count} | "
        f"Signals: {result.signal_count} | "
        f"Picks: {len(result.recommendations)}",
        style="dim",
    )
    if result.market_context and result.market_context.get("nifty"):
        nifty = result.market_context["nifty"]
        header.append(
            f"\nNifty 50: {nifty.get('level', '?')} "
            f"({nifty.get('change_pct', 0):+.2f}%) | "
            f"Regime: {nifty.get('regime', '?')}",
            style="dim",
        )
    console.print(Panel(header, title="Multi-Agent Orchestrator", border_style="cyan"))

    if result.errors:
        for err in result.errors:
            console.print(f"  [red]! {err}[/red]")
        console.print()

    if not result.recommendations:
        console.print("\n[yellow]No swing trade recommendations at this time.[/yellow]\n")
        _print_disclaimer(console)
        return

    # Summary table
    table = Table(title="Top Swing Trade Recommendations", show_lines=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Symbol", style="cyan bold", width=14)
    table.add_column("Signal", width=12)
    table.add_column("Conf", justify="right", width=6)
    table.add_column("Entry", justify="right", width=10)
    table.add_column("SL", justify="right", width=10)
    table.add_column("T1", justify="right", width=10)
    table.add_column("T2", justify="right", width=10)
    table.add_column("R:R", justify="right", width=5)
    table.add_column("Qty", justify="right", width=6)
    table.add_column("Risk", justify="right", width=10)
    table.add_column("Setup", width=14)

    for idx, rec in enumerate(result.recommendations, 1):
        style = _signal_style(rec.signal)
        table.add_row(
            str(idx),
            rec.symbol,
            Text(rec.signal.value, style=style),
            f"{rec.confidence:.0%}",
            f"{rec.entry_price:,.2f}",
            f"{rec.stop_loss:,.2f}",
            f"{rec.target_1:,.2f}",
            f"{rec.target_2:,.2f}",
            f"{rec.risk_reward_ratio:.1f}",
            str(rec.position_size_shares),
            f"{rec.risk_amount:,.0f}",
            rec.setup_type,
        )

    console.print(table)

    # Detailed cards
    console.print("\n[bold]Detailed Analysis[/bold]\n")
    for idx, rec in enumerate(result.recommendations, 1):
        style = _signal_style(rec.signal)
        title = f"#{idx}  {rec.symbol}  |  {rec.sector}"
        lines = [
            f"[{style}]{rec.signal.value}[/{style}]  "
            f"Confidence: {rec.confidence:.0%}  |  "
            f"Setup: {rec.setup_type}  |  Trend: {rec.trend.value}",
            "",
            f"  Entry:     ₹{rec.entry_price:>10,.2f}",
            f"  Stop-Loss: ₹{rec.stop_loss:>10,.2f}",
            f"  Target 1:  ₹{rec.target_1:>10,.2f}",
            f"  Target 2:  ₹{rec.target_2:>10,.2f}",
            f"  Target 3:  ₹{rec.target_3:>10,.2f}",
            "",
            f"  R:R: {rec.risk_reward_ratio:.1f}  |  "
            f"Qty: {rec.position_size_shares}  |  "
            f"Value: ₹{rec.position_value:,.0f}  |  "
            f"Risk: ₹{rec.risk_amount:,.0f}",
            f"  RSI: {rec.rsi:.1f}  |  ADX: {rec.adx:.1f}  |  "
            f"Vol: {rec.volume_ratio:.1f}x  |  "
            f"Weekly: {rec.weekly_trend}  |  "
            f"Base: {rec.base_status}",
            "",
            "[dim]Reasons:[/dim]",
        ]
        for reason in rec.reasons:
            lines.append(f"  - {reason}")
        console.print(Panel("\n".join(lines), title=title, border_style=style))

    _print_disclaimer(console)


def _print_disclaimer(console) -> None:
    console.print(Panel(
        "[yellow]DISCLAIMER: Educational / informational only. Not financial "
        "advice. Always do your own research. Trading involves risk of loss.[/yellow]",
        border_style="yellow",
    ))


def _signal_style(signal: Signal) -> str:
    return {
        Signal.STRONG_BUY: "bold green",
        Signal.BUY: "green",
        Signal.HOLD: "yellow",
        Signal.SELL: "red",
        Signal.STRONG_SELL: "bold red",
    }.get(signal, "white")


# ── Plain-text fallback ───────────────────────────────────────────────────

def _display_plain(result: OrchestratorResult) -> None:
    print("=" * 60)
    print("Swing Sentinel — Multi-Agent Analysis")
    print(f"Time: {result.timestamp:%Y-%m-%d %H:%M}")
    print(f"Scanned: {result.scanned_count} | Filtered: {result.filtered_count} "
          f"| Signals: {result.signal_count} | Picks: {len(result.recommendations)}")
    print("=" * 60)

    if result.errors:
        for err in result.errors:
            print(f"  ! {err}")

    if not result.recommendations:
        print("\nNo swing trade recommendations at this time.\n")
        return

    for idx, rec in enumerate(result.recommendations, 1):
        print(f"\n#{idx}  {rec.symbol} ({rec.sector})")
        print(f"  Signal: {rec.signal.value}  Confidence: {rec.confidence:.0%}")
        print(f"  Entry: {rec.entry_price:,.2f}  SL: {rec.stop_loss:,.2f}  "
              f"T1: {rec.target_1:,.2f}  T2: {rec.target_2:,.2f}")
        print(f"  R:R: {rec.risk_reward_ratio:.1f}  Qty: {rec.position_size_shares}  "
              f"Risk: {rec.risk_amount:,.0f}")
        print(f"  Setup: {rec.setup_type}  RSI: {rec.rsi:.1f}  Vol: {rec.volume_ratio:.1f}x")
        for reason in rec.reasons:
            print(f"    - {reason}")

    print("\nDISCLAIMER: Educational only. Not financial advice.")
