#!/usr/bin/env python3
"""
Auto-generate charts and latency plots from recv_log.csv
Usage: python3 plot_analysis.py [csv_file]
"""

import csv
import sys
import matplotlib.pyplot as plt
import numpy as np
from collections import defaultdict


def load_data(csv_file="recv_log.csv"):
    """Load and parse the CSV log file."""
    data = []
    with open(csv_file, "r") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) >= 5:
                arrival = int(row[0])
                channel = int(row[1])  # 0=reliable, 1=unreliable
                seq = int(row[2])
                sent_ts = int(row[3])
                payload = row[4]
                data.append(
                    {
                        "arrival": arrival,
                        "channel": channel,
                        "seq": seq,
                        "sent_ts": sent_ts,
                        "latency": arrival - sent_ts,  # milliseconds
                        "payload": payload,
                    }
                )
    return data


def plot_latency_over_time(data):
    """Plot latency over time for reliable and unreliable channels."""
    reliable = [d for d in data if d["channel"] == 0]
    unreliable = [d for d in data if d["channel"] == 1]

    plt.figure(figsize=(12, 6))

    if reliable:
        r_times = [
            (d["arrival"] - data[0]["arrival"]) / 1000 for d in reliable
        ]  # seconds
        r_latencies = [d["latency"] for d in reliable]
        plt.scatter(
            r_times, r_latencies, alpha=0.6, s=30, label="Reliable", color="blue"
        )

    if unreliable:
        u_times = [(d["arrival"] - data[0]["arrival"]) / 1000 for d in unreliable]
        u_latencies = [d["latency"] for d in unreliable]
        plt.scatter(
            u_times, u_latencies, alpha=0.6, s=30, label="Unreliable", color="orange"
        )

    plt.xlabel("Time (seconds)", fontsize=12)
    plt.ylabel("Latency (ms)", fontsize=12)
    plt.title("Packet Latency Over Time", fontsize=14, fontweight="bold")
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig("latency_over_time.png", dpi=150)
    print("✓ Saved: latency_over_time.png")


def plot_latency_distribution(data):
    """Plot latency distribution histogram."""
    reliable = [d["latency"] for d in data if d["channel"] == 0]
    unreliable = [d["latency"] for d in data if d["channel"] == 1]

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Reliable channel
    if reliable:
        axes[0].hist(reliable, bins=30, alpha=0.7, color="blue", edgecolor="black")
        axes[0].axvline(
            np.mean(reliable),
            color="red",
            linestyle="--",
            label=f"Mean: {np.mean(reliable):.1f}ms",
        )
        axes[0].set_xlabel("Latency (ms)", fontsize=11)
        axes[0].set_ylabel("Frequency", fontsize=11)
        axes[0].set_title("Reliable Channel Latency Distribution", fontweight="bold")
        axes[0].legend()
        axes[0].grid(True, alpha=0.3)

    # Unreliable channel
    if unreliable:
        axes[1].hist(unreliable, bins=30, alpha=0.7, color="orange", edgecolor="black")
        axes[1].axvline(
            np.mean(unreliable),
            color="red",
            linestyle="--",
            label=f"Mean: {np.mean(unreliable):.1f}ms",
        )
        axes[1].set_xlabel("Latency (ms)", fontsize=11)
        axes[1].set_ylabel("Frequency", fontsize=11)
        axes[1].set_title("Unreliable Channel Latency Distribution", fontweight="bold")
        axes[1].legend()
        axes[1].grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig("latency_distribution.png", dpi=150)
    print("✓ Saved: latency_distribution.png")


def plot_throughput(data):
    """Plot throughput (packets per second) over time."""
    if not data:
        return

    # Calculate throughput in 1-second windows
    start_time = data[0]["arrival"]
    end_time = data[-1]["arrival"]
    duration_sec = (end_time - start_time) / 1000

    # Count packets in 1-second bins
    bins = defaultdict(lambda: {"reliable": 0, "unreliable": 0})
    for d in data:
        bin_idx = int((d["arrival"] - start_time) / 1000)  # 1-second bins
        if d["channel"] == 0:
            bins[bin_idx]["reliable"] += 1
        else:
            bins[bin_idx]["unreliable"] += 1

    times = sorted(bins.keys())
    reliable_counts = [bins[t]["reliable"] for t in times]
    unreliable_counts = [bins[t]["unreliable"] for t in times]

    plt.figure(figsize=(12, 6))
    plt.plot(times, reliable_counts, marker="o", label="Reliable", linewidth=2)
    plt.plot(times, unreliable_counts, marker="s", label="Unreliable", linewidth=2)
    plt.xlabel("Time (seconds)", fontsize=12)
    plt.ylabel("Packets per Second", fontsize=12)
    plt.title("Throughput Over Time", fontsize=14, fontweight="bold")
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig("throughput.png", dpi=150)
    print("✓ Saved: throughput.png")


def print_statistics(data):
    """Print summary statistics."""
    reliable = [d for d in data if d["channel"] == 0]
    unreliable = [d for d in data if d["channel"] == 1]

    print("\n" + "=" * 60)
    print("STATISTICS SUMMARY")
    print("=" * 60)

    print(f"\nTotal Packets: {len(data)}")
    print(f"  Reliable:   {len(reliable)} ({len(reliable)/len(data)*100:.1f}%)")
    print(f"  Unreliable: {len(unreliable)} ({len(unreliable)/len(data)*100:.1f}%)")

    if reliable:
        r_latencies = [d["latency"] for d in reliable]
        print(f"\nReliable Channel Latency:")
        print(f"  Mean:   {np.mean(r_latencies):.2f} ms")
        print(f"  Median: {np.median(r_latencies):.2f} ms")
        print(f"  Std:    {np.std(r_latencies):.2f} ms")
        print(f"  Min:    {np.min(r_latencies):.2f} ms")
        print(f"  Max:    {np.max(r_latencies):.2f} ms")

    if unreliable:
        u_latencies = [d["latency"] for d in unreliable]
        print(f"\nUnreliable Channel Latency:")
        print(f"  Mean:   {np.mean(u_latencies):.2f} ms")
        print(f"  Median: {np.median(u_latencies):.2f} ms")
        print(f"  Std:    {np.std(u_latencies):.2f} ms")
        print(f"  Min:    {np.min(u_latencies):.2f} ms")
        print(f"  Max:    {np.max(u_latencies):.2f} ms")

    # Check for packet loss
    all_seqs = sorted(set(d["seq"] for d in data))
    if all_seqs:
        expected = list(range(all_seqs[0], all_seqs[-1] + 1))
        missing = set(expected) - set(all_seqs)
        if missing:
            print(f"\n⚠️  Missing sequences: {sorted(missing)}")
            print(
                f"   Packet loss: {len(missing)} packets ({len(missing)/len(expected)*100:.1f}%)"
            )

    duration = (data[-1]["arrival"] - data[0]["arrival"]) / 1000
    print(f"\nDuration: {duration:.2f} seconds")
    print(f"Average throughput: {len(data)/duration:.2f} packets/sec")
    print("=" * 60)


def main():
    csv_file = sys.argv[1] if len(sys.argv) > 1 else "recv_log.csv"

    print(f"\n📊 Analyzing {csv_file}...")

    try:
        data = load_data(csv_file)
        if not data:
            print("❌ No data found in CSV file!")
            return

        print(f"✓ Loaded {len(data)} packets\n")

        # Generate all plots
        print("Generating plots...")
        plot_latency_over_time(data)
        plot_latency_distribution(data)
        plot_throughput(data)

        # Print statistics
        print_statistics(data)

        print("\n✅ Analysis complete! Generated files:")
        print("   - latency_over_time.png")
        print("   - latency_distribution.png")
        print("   - throughput.png")
        print("   - sequence_timeline.png")
        print("\nOpen these PNG files to view your charts! 📈\n")

    except FileNotFoundError:
        print(f"❌ Error: File '{csv_file}' not found!")
        print("   Make sure receiver.py has been run and generated recv_log.csv")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
