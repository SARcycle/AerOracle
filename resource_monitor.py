"""CPU and RAM use of this process while an operation runs (e.g. a weather API call), for the usage statistics."""

import psutil
import time
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional, List
import statistics


@dataclass
class ResourceSnapshot:
    """Single point-in-time resource measurement"""
    timestamp: float
    cpu_percent: float  # CPU usage %
    memory_mb: float    # RAM usage in MB
    memory_percent: float  # RAM usage %


@dataclass
class ResourceMetrics:
    """Aggregated resource metrics for an operation"""
    duration_seconds: float
    
    # CPU metrics
    cpu_min: float
    cpu_max: float
    cpu_mean: float
    cpu_p95: float  # 95th percentile
    
    # Memory metrics
    memory_min_mb: float
    memory_max_mb: float
    memory_mean_mb: float
    memory_p95_mb: float
    
    # Memory percent
    memory_percent_min: float
    memory_percent_max: float
    memory_percent_mean: float
    
    # Sample count
    samples: int


class ResourceMonitor:
    """CPU and RAM use of this process, sampled in a background thread between start_monitoring() and stop_monitoring()."""
    
    def __init__(self, sample_interval: float = 0.1):
        """sample_interval: time between samples [s]."""
        self.sample_interval = sample_interval
        self.process = psutil.Process()
        self._monitoring = False
        self._snapshots: List[ResourceSnapshot] = []
        self._monitor_thread: Optional[threading.Thread] = None
    
    def _monitor_loop(self):
        """Background thread that samples resources"""
        # The first cpu_percent() call only sets the baseline
        self.process.cpu_percent()
        
        while self._monitoring:
            try:
                # Blocks for sample_interval: CPU use over that interval
                cpu_percent = self.process.cpu_percent(interval=self.sample_interval)
                memory_info = self.process.memory_info()
                memory_mb = memory_info.rss / (1024 * 1024)  # Convert to MB
                memory_percent = self.process.memory_percent()
                
                snapshot = ResourceSnapshot(
                    timestamp=time.time(),
                    cpu_percent=cpu_percent,
                    memory_mb=memory_mb,
                    memory_percent=memory_percent
                )
                self._snapshots.append(snapshot)
                
            except Exception:
                pass  # Process might have ended
    
    def start_monitoring(self):
        """Start collecting resource samples"""
        if not self._monitoring:
            self._monitoring = True
            self._snapshots = []
            self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self._monitor_thread.start()
    
    def stop_monitoring(self) -> ResourceMetrics:
        """Stop sampling; min, max, mean and 95th percentile of CPU and RAM use (zeros without samples)."""
        self._monitoring = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=1.0)
        
        if not self._snapshots:
            return ResourceMetrics(
                duration_seconds=0,
                cpu_min=0, cpu_max=0, cpu_mean=0, cpu_p95=0,
                memory_min_mb=0, memory_max_mb=0, memory_mean_mb=0, memory_p95_mb=0,
                memory_percent_min=0, memory_percent_max=0, memory_percent_mean=0,
                samples=0
            )
        
        duration = self._snapshots[-1].timestamp - self._snapshots[0].timestamp
        
        cpu_values = [s.cpu_percent for s in self._snapshots]
        memory_mb_values = [s.memory_mb for s in self._snapshots]
        memory_pct_values = [s.memory_percent for s in self._snapshots]
        
        def percentile_95(values):
            if not values:
                return 0
            sorted_values = sorted(values)
            index = int(len(sorted_values) * 0.95)
            return sorted_values[min(index, len(sorted_values) - 1)]
        
        metrics = ResourceMetrics(
            duration_seconds=duration,
            
            cpu_min=min(cpu_values),
            cpu_max=max(cpu_values),
            cpu_mean=statistics.mean(cpu_values),
            cpu_p95=percentile_95(cpu_values),
            
            memory_min_mb=min(memory_mb_values),
            memory_max_mb=max(memory_mb_values),
            memory_mean_mb=statistics.mean(memory_mb_values),
            memory_p95_mb=percentile_95(memory_mb_values),
            
            memory_percent_min=min(memory_pct_values),
            memory_percent_max=max(memory_pct_values),
            memory_percent_mean=statistics.mean(memory_pct_values),
            
            samples=len(self._snapshots)
        )
        
        return metrics


_monitor = ResourceMonitor()


@contextmanager
def track_resources():
    """Samples CPU and RAM use from entering the block until monitor.get_metrics() is called."""
    _monitor.start_monitoring()
    
    class MonitorContext:
        def get_metrics(self) -> ResourceMetrics:
            return _monitor.stop_monitoring()
    
    context = MonitorContext()
    
    try:
        yield context
    finally:
        pass  # Monitoring stops in get_metrics()


if __name__ == "__main__":
    print("Testing resource monitor...")
    
    with track_resources() as monitor:
        import numpy as np
        for i in range(10):
            data = np.random.rand(1000, 1000)
            result = np.sum(data)
            time.sleep(0.1)
    
    metrics = monitor.get_metrics()
    
    print("\nResource usage:")
    print(f"Duration: {metrics.duration_seconds:.2f}s")
    print(f"CPU: {metrics.cpu_min:.1f}% - {metrics.cpu_max:.1f}% (mean: {metrics.cpu_mean:.1f}%, p95: {metrics.cpu_p95:.1f}%)")
    print(f"RAM: {metrics.memory_min_mb:.0f}MB - {metrics.memory_max_mb:.0f}MB (mean: {metrics.memory_mean_mb:.0f}MB, p95: {metrics.memory_p95_mb:.0f}MB)")
    print(f"Samples: {metrics.samples}")
