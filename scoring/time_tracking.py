"""
scoring/time_tracking.py — measure applicant form completion time and behavior.

Metrics tracked:
  • Total form duration (start to submit)
  • Per-question time (answer entry time)
  • Per-section time (group of questions)
  • Thinking time (pause before answer)
  • Edit time (if they revise)
  • Question-level timestamps
  • Hesitation patterns (questions with long pauses)
  • Completion velocity (words per second)

Storage: form_responses.json extended with "time_metrics" field per response.
"""

from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("volt_cv.scoring")


# ══════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class QuestionTiming:
    """Timing for one question's answer."""
    question_id: str
    question_text: str
    start_time: float  # unix timestamp when field became active
    end_time: float    # unix timestamp when answer submitted
    thinking_time: float  # seconds before first keystroke
    typing_time: float    # seconds of active typing
    total_time: float     # end_time - start_time
    edit_count: int       # number of times they edited
    word_count: int       # final answer word count
    typing_speed: float   # words per minute (word_count / (typing_time/60))

    @property
    def is_long_pause(self, threshold_sec=30) -> bool:
        """True if they paused >30 seconds before typing."""
        return self.thinking_time >= threshold_sec

    @property
    def is_rushed(self, threshold_wpm=80) -> bool:
        """True if typing speed >80 WPM (likely copying/pasting or careless)."""
        return self.typing_speed > threshold_wpm if self.typing_time > 0 else False

    @property
    def is_careful(self, threshold_wpm=20) -> bool:
        """True if typing speed <20 WPM (careful, deliberate typing)."""
        return self.typing_speed < threshold_wpm if self.typing_time > 0 else False

    def to_dict(self) -> dict:
        return {
            'question_id': self.question_id,
            'question_text': self.question_text,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'thinking_time_sec': round(self.thinking_time, 2),
            'typing_time_sec': round(self.typing_time, 2),
            'total_time_sec': round(self.total_time, 2),
            'edit_count': self.edit_count,
            'word_count': self.word_count,
            'typing_speed_wpm': round(self.typing_speed, 1),
            'flags': {
                'long_pause': self.is_long_pause,
                'rushed': self.is_rushed,
                'careful': self.is_careful,
            }
        }


@dataclass
class SectionTiming:
    """Timing for one form section (group of questions)."""
    section_name: str
    section_number: int
    start_time: float
    end_time: float
    question_timings: List[QuestionTiming] = field(default_factory=list)
    average_time_per_question: float = 0.0

    def calculate(self):
        if self.question_timings:
            self.average_time_per_question = (
                sum(q.total_time for q in self.question_timings) / len(self.question_timings)
            )

    @property
    def total_time(self) -> float:
        return self.end_time - self.start_time

    def to_dict(self) -> dict:
        self.calculate()
        return {
            'section_name': self.section_name,
            'section_number': self.section_number,
            'total_time_sec': round(self.total_time, 2),
            'avg_time_per_question_sec': round(self.average_time_per_question, 2),
            'questions_count': len(self.question_timings),
            'questions': [q.to_dict() for q in self.question_timings],
        }


@dataclass
class FormTimingMetrics:
    """Overall form completion timing and analysis."""
    response_id: str
    form_id: str
    form_start_time: float      # when user opened form
    form_submit_time: float     # when user clicked submit
    total_duration_sec: float   # submit_time - start_time
    section_timings: List[SectionTiming] = field(default_factory=list)
    paused_and_resumed: int = 0  # times they closed and reopened
    total_thinking_time: float = 0.0
    total_typing_time: float = 0.0
    average_answer_time: float = 0.0
    
    # Behavior flags
    rushed: bool = False        # overall typing speed >90 WPM
    careful: bool = False       # overall typing speed <25 WPM
    multi_attempt: bool = False # submitted, edited, resubmitted
    high_pause_ratio: bool = False  # >40% of time is thinking

    def calculate(self):
        """Calculate derived metrics."""
        if not self.section_timings:
            return

        total_questions = 0
        all_timings = []

        for section in self.section_timings:
            section.calculate()
            all_timings.extend(section.question_timings)
            total_questions += len(section.question_timings)

        if all_timings:
            self.total_thinking_time = sum(q.thinking_time for q in all_timings)
            self.total_typing_time = sum(q.typing_time for q in all_timings)
            self.average_answer_time = sum(q.total_time for q in all_timings) / len(all_timings)

            # Overall typing speed
            total_words = sum(q.word_count for q in all_timings)
            if self.total_typing_time > 0:
                overall_wpm = (total_words / self.total_typing_time) * 60
                self.rushed = overall_wpm > 90
                self.careful = overall_wpm < 25

            # Pause ratio
            if self.total_duration_sec > 0:
                pause_ratio = self.total_thinking_time / self.total_duration_sec
                self.high_pause_ratio = pause_ratio > 0.40

    def to_dict(self) -> dict:
        self.calculate()
        return {
            'response_id': self.response_id,
            'form_id': self.form_id,
            'form_start_time': self.form_start_time,
            'form_submit_time': self.form_submit_time,
            'total_duration_sec': round(self.total_duration_sec, 2),
            'total_duration_min': round(self.total_duration_sec / 60, 1),
            'total_thinking_time_sec': round(self.total_thinking_time, 2),
            'total_typing_time_sec': round(self.total_typing_time, 2),
            'average_answer_time_sec': round(self.average_answer_time, 2),
            'sections': [s.to_dict() for s in self.section_timings],
            'behavior_flags': {
                'rushed': self.rushed,
                'careful': self.careful,
                'multi_attempt': self.multi_attempt,
                'high_pause_ratio': self.high_pause_ratio,
                'paused_and_resumed': self.paused_and_resumed,
            }
        }


# ══════════════════════════════════════════════════════════════════════════
# ANALYSIS & INSIGHTS
# ══════════════════════════════════════════════════════════════════════════

class FormTimingAnalyzer:
    """Analyze timing metrics to infer candidate behavior."""

    @staticmethod
    def categorize_completion_speed(total_sec: float, question_count: int) -> str:
        """Categorize based on average time per question."""
        avg_per_q = total_sec / question_count if question_count > 0 else 0
        if avg_per_q < 30:    return "Rushed"
        if avg_per_q < 60:    return "Normal"
        if avg_per_q < 120:   return "Thoughtful"
        return "Very Deliberate"

    @staticmethod
    def detect_copying_patterns(timings: List[QuestionTiming]) -> List[str]:
        """Detect signs of copy-paste or AI-generated content."""
        flags = []
        high_speed_qs = [q for q in timings if q.is_rushed]
        if len(high_speed_qs) / len(timings) > 0.5:
            flags.append("Multiple rushed answers suggest possible copying")

        # Check for identical or very similar thinking time across questions
        thinking_times = [q.thinking_time for q in timings if q.thinking_time > 0]
        if thinking_times and len(thinking_times) > 3:
            avg_thinking = sum(thinking_times) / len(thinking_times)
            similar_count = sum(1 for t in thinking_times if abs(t - avg_thinking) < 2)
            if similar_count / len(thinking_times) > 0.7:
                flags.append("Unusually uniform thinking time across questions")

        return flags

    @staticmethod
    def detect_genuine_effort(timings: List[QuestionTiming]) -> List[str]:
        """Detect signs of genuine, thoughtful responses."""
        signals = []
        careful_qs = [q for q in timings if q.is_careful]
        if len(careful_qs) / len(timings) > 0.5:
            signals.append("Careful typing style suggests deliberate composition")

        long_pause_qs = [q for q in timings if q.is_long_pause]
        if len(long_pause_qs) / len(timings) > 0.3:
            signals.append("Frequent long pauses suggest thoughtful consideration")

        edited_qs = [q for q in timings if q.edit_count > 0]
        if len(edited_qs) / len(timings) > 0.3:
            signals.append("Multiple edits/revisions suggest careful review")

        return signals

    @staticmethod
    def get_completion_insights(metrics: FormTimingMetrics) -> dict:
        """Generate human-readable insights from timing data."""
        insights = {
            'completion_time': f"{metrics.total_duration_sec / 60:.1f} minutes",
            'speed_category': FormTimingAnalyzer.categorize_completion_speed(
                metrics.total_duration_sec,
                sum(len(s.question_timings) for s in metrics.section_timings)
            ),
            'slowest_section': None,
            'fastest_section': None,
            'average_question_time': f"{metrics.average_answer_time:.0f} seconds",
            'thinking_vs_typing_ratio': None,
            'possible_patterns': [],
            'genuine_effort_signals': [],
        }

        # Find slowest/fastest sections
        if metrics.section_timings:
            by_time = sorted(metrics.section_timings, key=lambda s: s.total_time)
            insights['fastest_section'] = {
                'name': by_time[0].section_name,
                'time_sec': f"{by_time[0].total_time:.0f}",
            }
            insights['slowest_section'] = {
                'name': by_time[-1].section_name,
                'time_sec': f"{by_time[-1].total_time:.0f}",
            }

        # Thinking vs typing
        if metrics.total_typing_time > 0:
            ratio = metrics.total_thinking_time / metrics.total_typing_time
            insights['thinking_vs_typing_ratio'] = f"{ratio:.2f}:1"

        # Pattern detection
        all_qs = [q for s in metrics.section_timings for q in s.question_timings]
        if all_qs:
            copying_flags = FormTimingAnalyzer.detect_copying_patterns(all_qs)
            genuine_flags = FormTimingAnalyzer.detect_genuine_effort(all_qs)
            insights['possible_patterns'] = copying_flags
            insights['genuine_effort_signals'] = genuine_flags

        return insights


# ══════════════════════════════════════════════════════════════════════════
# FRONTEND EVENT LISTENERS (JavaScript to send to backend)
# ══════════════════════════════════════════════════════════════════════════

FRONTEND_TRACKING_CODE = """
// Add this to your form frontend (static/index.html)

class FormTimingTracker {
  constructor() {
    this.startTime = Date.now() / 1000;
    this.currentQuestion = null;
    this.questionStartTime = null;
    this.firstKeystrokeTime = null;
    this.sectionTimings = {};
    this.events = [];
  }

  onQuestionFocus(questionId, questionText, sectionName) {
    if (this.currentQuestion) {
      this.recordQuestionTiming();
    }
    this.currentQuestion = questionId;
    this.questionStartTime = Date.now() / 1000;
    this.firstKeystrokeTime = null;
  }

  onFirstKeystroke(questionId) {
    if (this.firstKeystrokeTime === null && this.questionStartTime) {
      this.firstKeystrokeTime = Date.now() / 1000;
    }
  }

  onQuestionBlur(questionId) {
    this.recordQuestionTiming();
  }

  recordQuestionTiming() {
    if (!this.currentQuestion || !this.questionStartTime) return;
    
    const endTime = Date.now() / 1000;
    const thinkingTime = this.firstKeystrokeTime
      ? this.firstKeystrokeTime - this.questionStartTime
      : 0;
    const typingTime = this.firstKeystrokeTime
      ? endTime - this.firstKeystrokeTime
      : 0;
    
    this.events.push({
      type: 'question_timing',
      question_id: this.currentQuestion,
      thinking_time: thinkingTime,
      typing_time: typingTime,
      total_time: endTime - this.questionStartTime,
    });
  }

  onFormSubmit() {
    this.recordQuestionTiming();
    const endTime = Date.now() / 1000;
    return {
      start_time: this.startTime,
      submit_time: endTime,
      total_duration: endTime - this.startTime,
      events: this.events,
    };
  }
}

// Usage in form:
const tracker = new FormTimingTracker();
document.addEventListener('focus', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
    tracker.onQuestionFocus(e.target.id, e.target.placeholder, e.target.dataset.section);
  }
}, true);

document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
    tracker.onFirstKeystroke(e.target.id);
  }
}, true);

document.getElementById('submit-btn').addEventListener('click', () => {
  const timingData = tracker.onFormSubmit();
  // Send timingData to backend with the form submission
  console.log('Form timing:', timingData);
});
"""


# ══════════════════════════════════════════════════════════════════════════
# STORAGE INTEGRATION
# ══════════════════════════════════════════════════════════════════════════

def add_timing_to_response(response_dict: dict, timing_metrics: FormTimingMetrics) -> dict:
    """Add timing data to a form response record before storing."""
    response_dict['time_metrics'] = timing_metrics.to_dict()
    
    # Add flags to help with evaluation
    metrics = timing_metrics.to_dict()
    response_dict['speed_category'] = FormTimingAnalyzer.categorize_completion_speed(
        timing_metrics.total_duration_sec,
        sum(len(s.question_timings) for s in timing_metrics.section_timings)
    )
    response_dict['completion_insights'] = FormTimingAnalyzer.get_completion_insights(timing_metrics)
    
    return response_dict


def load_timing_from_response(response_dict: dict) -> Optional[FormTimingMetrics]:
    """Reconstruct FormTimingMetrics from stored response."""
    if 'time_metrics' not in response_dict:
        return None
    
    data = response_dict['time_metrics']
    metrics = FormTimingMetrics(
        response_id=data.get('response_id'),
        form_id=data.get('form_id'),
        form_start_time=data.get('form_start_time', 0),
        form_submit_time=data.get('form_submit_time', 0),
        total_duration_sec=data.get('total_duration_sec', 0),
    )
    return metrics
