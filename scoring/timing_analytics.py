"""
scoring/timing_analytics.py — aggregate timing statistics and generate reports.
"""

from __future__ import annotations
from typing import List, Dict
from statistics import mean, median, stdev
from scoring.time_tracking import FormTimingMetrics, QuestionTiming
import json


class TimingAnalytics:
    """Aggregate timing data across multiple candidates."""

    def __init__(self):
        self.responses_with_timing: List[Dict] = []

    def add_response(self, response: Dict):
        """Add a candidate response with timing data."""
        if 'time_metrics' in response:
            self.responses_with_timing.append(response)

    def completion_time_stats(self) -> Dict:
        """Summary statistics on form completion time."""
        if not self.responses_with_timing:
            return {}

        times = [r['time_metrics']['total_duration_sec'] for r in self.responses_with_timing]
        return {
            'count': len(times),
            'average_sec': round(mean(times), 1),
            'median_sec': round(median(times), 1),
            'stdev_sec': round(stdev(times), 1) if len(times) > 1 else 0,
            'min_sec': round(min(times), 1),
            'max_sec': round(max(times), 1),
            'average_min': round(mean(times) / 60, 1),
            'median_min': round(median(times) / 60, 1),
        }

    def speed_distribution(self) -> Dict:
        """How many candidates are rushed/normal/thoughtful."""
        from scoring.time_tracking import FormTimingAnalyzer
        
        dist = {'Rushed': 0, 'Normal': 0, 'Thoughtful': 0, 'Very Deliberate': 0}
        
        for response in self.responses_with_timing:
            metrics = response['time_metrics']
            q_count = sum(len(s['questions']) for s in metrics.get('sections', []))
            if q_count == 0:
                continue
            category = FormTimingAnalyzer.categorize_completion_speed(
                metrics['total_duration_sec'],
                q_count
            )
            dist[category] = dist.get(category, 0) + 1

        return {k: v for k, v in sorted(dist.items(), key=lambda x: x[1], reverse=True)}

    def question_level_analysis(self) -> Dict:
        """Average time per question across all candidates."""
        if not self.responses_with_timing:
            return {}

        all_question_times = []
        for response in self.responses_with_timing:
            metrics = response['time_metrics']
            for section in metrics.get('sections', []):
                for q in section.get('questions', []):
                    all_question_times.append(q['total_time_sec'])

        if not all_question_times:
            return {}

        return {
            'avg_time_per_question_sec': round(mean(all_question_times), 1),
            'median_time_per_question_sec': round(median(all_question_times), 1),
            'fastest_question_sec': round(min(all_question_times), 1),
            'slowest_question_sec': round(max(all_question_times), 1),
        }

    def section_analysis(self) -> Dict:
        """Which sections take longest on average."""
        if not self.responses_with_timing:
            return {}

        section_times = {}
        for response in self.responses_with_timing:
            metrics = response['time_metrics']
            for section in metrics.get('sections', []):
                name = section['section_name']
                time = section['total_time_sec']
                if name not in section_times:
                    section_times[name] = []
                section_times[name].append(time)

        result = {}
        for name, times in section_times.items():
            result[name] = {
                'average_sec': round(mean(times), 1),
                'median_sec': round(median(times), 1),
                'count': len(times),
            }
        
        return dict(sorted(result.items(), key=lambda x: x[1]['average_sec'], reverse=True))

    def behavior_flags_summary(self) -> Dict:
        """Count of candidates flagged for suspicious behavior."""
        if not self.responses_with_timing:
            return {}

        flags = {
            'rushed': 0,
            'careful': 0,
            'multi_attempt': 0,
            'high_pause_ratio': 0,
        }

        for response in self.responses_with_timing:
            metrics = response['time_metrics']
            behavior = metrics.get('behavior_flags', {})
            for flag_name, flag_value in behavior.items():
                if flag_name in flags and flag_value:
                    flags[flag_name] += 1

        return flags

    def candidates_by_completion_speed(self, sort_by='slowest') -> List[Dict]:
        """List candidates ordered by completion speed."""
        candidates = []
        for response in self.responses_with_timing:
            metrics = response['time_metrics']
            q_count = sum(len(s['questions']) for s in metrics.get('sections', []))
            candidates.append({
                'email': response.get('email', 'unknown'),
                'response_id': response.get('response_id'),
                'total_time_min': round(metrics['total_duration_sec'] / 60, 1),
                'total_time_sec': metrics['total_duration_sec'],
                'avg_per_question_sec': round(
                    metrics['total_duration_sec'] / q_count, 1) if q_count > 0 else 0,
                'speed_category': response.get('speed_category', 'Unknown'),
            })

        if sort_by == 'slowest':
            candidates.sort(key=lambda x: x['total_time_sec'], reverse=True)
        elif sort_by == 'fastest':
            candidates.sort(key=lambda x: x['total_time_sec'])

        return candidates

    def generate_report(self) -> Dict:
        """Complete timing analysis report."""
        return {
            'total_responses_with_timing': len(self.responses_with_timing),
            'completion_time_stats': self.completion_time_stats(),
            'speed_distribution': self.speed_distribution(),
            'question_level_analysis': self.question_level_analysis(),
            'section_analysis': self.section_analysis(),
            'behavior_flags': self.behavior_flags_summary(),
            'slowest_candidates': self.candidates_by_completion_speed('slowest')[:10],
            'fastest_candidates': self.candidates_by_completion_speed('fastest')[:10],
        }


def timing_report_for_api() -> Dict:
    """
    Get timing analytics from form_responses.json and return as JSON.
    Can be called as an API endpoint: GET /api/timing-analytics
    """
    from config import OUTPUT_DIR
    import json as j

    responses_file = OUTPUT_DIR / "form_responses.json"
    if not responses_file.exists():
        return {'error': 'No form responses found'}

    data = j.loads(responses_file.read_text())
    responses = data.get('responses', [])

    analytics = TimingAnalytics()
    for response in responses:
        analytics.add_response(response)

    return analytics.generate_report()
