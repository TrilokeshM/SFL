"""
Turns extracted code evidence into:
  1) suggested skills (constrained to the provided catalog)
  2) interview questions per suggested skill
  3) an outcome-by-outcome evaluation summary

All three are LLM calls that are grounded in the actual file tree /
dependencies / snippets we extracted, and are asked to return strict JSON
that we validate against our Pydantic schemas before returning it.
"""
from __future__ import annotations
import json
from typing import List, Tuple

from app.zip_analyzer import CodeEvidence
from app.llm_client import call_json, call_json_async
from app.schemas import SuggestedSkill, SkillQuestions, Question, EvaluationSummary, OutcomeEvaluation


def _evidence_prompt(evidence: CodeEvidence) -> str:
    tree = "\n".join(evidence.file_tree[:200])
    deps = "\n\n".join(f"--- {name} ---\n{content[:2000]}" for name, content in evidence.dependencies.items())
    snippets = "\n\n".join(
        f"--- {path} ---\n{content[:1500]}" for path, content in list(evidence.snippets.items())[:25]
    )
    return (
        f"FILE TREE ({len(evidence.file_tree)} files):\n{tree}\n\n"
        f"DEPENDENCY FILES:\n{deps or '(none found)'}\n\n"
        f"CODE SNIPPETS (truncated):\n{snippets or '(none readable)'}"
    )


async def suggest_skills(evidence: CodeEvidence, catalog: List[dict]) -> Tuple[List[SuggestedSkill], int]:
    catalog_json = json.dumps(catalog, indent=2)
    system = (
        "You are a strict technical reviewer. You suggest skills a student demonstrated in a "
        "codebase, but you may ONLY choose from the provided skill catalog — never invent new "
        "skill names or skill_ids. If nothing in the catalog fits well, return fewer entries "
        "rather than forcing a poor match. Respond with ONLY a JSON array, no prose, no markdown fences."
    )
    user = (
        f"SKILL CATALOG (only choose skill_id/skill_name pairs from here, verbatim):\n{catalog_json}\n\n"
        f"CODEBASE EVIDENCE:\n{_evidence_prompt(evidence)}\n\n"
        "Return a JSON array of objects, each with exactly these keys:\n"
        '  "skill_id" (must match a catalog entry exactly),\n'
        '  "skill_name" (must match that same catalog entry exactly),\n'
        '  "confidence" (number 0-1),\n'
        '  "rationale" (one sentence citing specific evidence, e.g. a file name or dependency).\n'
        "Only include skills you have real evidence for."
    )
    parsed, tokens = await call_json_async(system, user)
    
    # Robustly handle if the LLM returns an object like {"skills": [...]} instead of an array
    if isinstance(parsed, dict):
        for val in parsed.values():
            if isinstance(val, list):
                parsed = val
                break
        else:
            parsed = []
            
    if not isinstance(parsed, list):
        parsed = []

    catalog_by_id = {c["skill_id"]: c["skill_name"] for c in catalog}
    catalog_by_name = {c["skill_name"].lower(): c for c in catalog}
    
    results: List[SuggestedSkill] = []
    for item in parsed:
        skill_id = item.get("skill_id")
        skill_name = item.get("skill_name", "")
        
        matched_id = None
        matched_name = None

        if skill_id in catalog_by_id:
            matched_id = skill_id
            matched_name = catalog_by_id[skill_id]
        elif skill_name.lower() in catalog_by_name:
            matched_id = catalog_by_name[skill_name.lower()]["skill_id"]
            matched_name = catalog_by_name[skill_name.lower()]["skill_name"]
            
        if not matched_id:
            continue

        results.append(SuggestedSkill(
            skill_id=matched_id,
            skill_name=matched_name,
            confidence=float(item.get("confidence", 0.5)),
            rationale=str(item.get("rationale", "")).strip() or "No rationale provided.",
        ))
    return results, tokens


async def generate_questions(
    evidence: CodeEvidence, skills: List[SuggestedSkill], questions_per_skill: int
) -> Tuple[List[SkillQuestions], int]:
    skill_names = [s.skill_name for s in skills]
    if not skill_names:
        skill_names = ["General Architecture & Best Practices"]

    import uuid
    random_seed = uuid.uuid4().hex
    
    system = (
        "You are an experienced mentor preparing viva questions for a student's project. "
        "For each skill, write questions that reference the ACTUAL codebase evidence given — "
        "cite real file paths, function names, or config values wherever possible. Avoid generic "
        "textbook questions that could apply to any project. Respond with ONLY a JSON array, no prose.\n"
        f"[RANDOMIZATION SEED: {random_seed}] Generate HIGHLY DIVERSE and UNIQUE questions. "
        "Do not repeat common questions. Select random files or less obvious aspects to ask about."
    )
    user = (
        f"SKILLS TO COVER: {json.dumps(skill_names)}\n"
        f"QUESTIONS PER SKILL: {questions_per_skill} minimum "
        f"(YOU MUST GENERATE AT LEAST 1 'conceptual' AND AT LEAST 1 'codebase_specific' question per skill). "
        f"Codebase-specific questions MUST cite real file paths or symbols from the evidence.\n\n"
        f"CODEBASE EVIDENCE:\n{_evidence_prompt(evidence)}\n\n"
        "Return a strictly valid JSON array where each item has:\n"
        '  "skill_name" (must exactly match one of the SKILLS TO COVER),\n'
        '  "questions": array of objects each with:\n'
        '      "type": "conceptual" or "codebase_specific",\n'
        '      "question": string,\n'
        '      "references": array of file paths / symbol names the question cites '
        '(MUST NOT BE EMPTY for "codebase_specific" questions, empty for pure "conceptual" questions).'
    )
    parsed, tokens = await call_json_async(system, user, max_tokens=4000, temperature=0.8)
    
    if isinstance(parsed, dict):
        for val in parsed.values():
            if isinstance(val, list):
                parsed = val
                break
        else:
            parsed = []
            
    if not isinstance(parsed, list):
        parsed = []

    results: List[SkillQuestions] = []
    for item in parsed:
        name = item.get("skill_name")
        if name not in skill_names:
            continue
        questions = []
        for q in item.get("questions", []):
            qtype = q.get("type")
            if qtype not in ("conceptual", "codebase_specific"):
                continue
            questions.append(Question(
                type=qtype,
                question=str(q.get("question", "")).strip(),
                references=[str(r) for r in q.get("references", [])],
            ))
        if questions:
            results.append(SkillQuestions(skill_name=name, questions=questions))
    return results, tokens


async def evaluate_outcomes(
    evidence: CodeEvidence, project_title: str, project_description: str, outcomes_raw: str
) -> Tuple[EvaluationSummary, int]:
    outcomes = _split_outcomes(outcomes_raw)
    system = (
        "You are a strict technical mentor evaluating whether a student's codebase actually "
        "You are an expert technical assessor analyzing a student project submission.\n"
        "Your task is to evaluate the provided code evidence against the specific expected outcomes.\n"
        "Provide a highly detailed, professional, and comprehensive analysis.\n"
        "1. For each outcome, determine the status (met, partial, not_met, or not_verifiable).\n"
        "2. Provide concrete, technical evidence citing specific files, design patterns, or code snippets from the codebase.\n"
        "3. Identify specific technical gaps if the outcome is not fully met.\n"
        "4. Determine the overall_alignment (strong, partial, weak) and a score (0.0 to 1.0).\n"
        "5. Write a professional narrative (3-5 sentences) summarizing the technical architecture, code quality, and extent of achievement. This should read like a premium audit report.\n"
        "6. List specific technical strengths and gaps.\n"
        "Respond strictly with a JSON object matching this schema:\n"
        "{\n"
        '  "overall_alignment": "strong",\n'
        '  "alignment_score": 0.95,\n'
        '  "narrative": "Detailed 3-5 sentence paragraph...",\n'
        '  "outcome_evaluation": [\n'
        '    {"outcome": "...", "status": "...", "evidence": "Detailed technical evidence...", "gap": "..."}\n'
        "  ],\n"
        '  "strengths": ["...", "..."],\n'
        '  "gaps": ["..."]\n'
        "}"
    )
    user = (
        f"PROJECT TITLE: {project_title}\n"
        f"PROJECT DESCRIPTION: {project_description or '(not provided)'}\n"
        f"CLAIMED OUTCOMES:\n" + "\n".join(f"- {o}" for o in outcomes) + "\n\n"
        f"CODEBASE EVIDENCE:\n{_evidence_prompt(evidence)}\n\n"
        "Return a JSON object with exactly these keys:\n"
        '  "overall_alignment": "strong" | "partial" | "weak",\n'
        '  "alignment_score": number 0-1,\n'
        '  "narrative": 2-4 plain-English sentences for a mentor (no bullet points),\n'
        '  "outcome_evaluation": array, one entry per claimed outcome above, each with:\n'
        '      "outcome": the outcome text (verbatim),\n'
        '      "status": "met" | "partial" | "not_met" | "not_verifiable",\n'
        '      "evidence": string citing a real file/module/pattern,\n'
        '      "gap": string or null describing what is missing (null if met),\n'
        '  "strengths": array of short strings,\n'
        '  "gaps": array of short strings.'
    )
    parsed, tokens = await call_json_async(system, user, max_tokens=3000)

    outcome_evals = []
    for item in parsed.get("outcome_evaluation", []):
        status = item.get("status")
        if status not in ("met", "partial", "not_met", "not_verifiable"):
            status = "not_verifiable"
        outcome_evals.append(OutcomeEvaluation(
            outcome=str(item.get("outcome", "")),
            status=status,
            evidence=str(item.get("evidence", "")),
            gap=item.get("gap"),
        ))

    alignment = parsed.get("overall_alignment")
    if alignment not in ("strong", "partial", "weak"):
        alignment = "partial"

    summary = EvaluationSummary(
        overall_alignment=alignment,
        alignment_score=parsed.get("alignment_score"),
        narrative=str(parsed.get("narrative", "")).strip() or "No narrative generated.",
        outcome_evaluation=outcome_evals,
        strengths=[str(s) for s in parsed.get("strengths", [])],
        gaps=[str(g) for g in parsed.get("gaps", [])],
    )
    return summary, tokens


def _split_outcomes(raw: str) -> List[str]:
    import re
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    cleaned = []
    for line in lines:
        line = re.sub(r"^\s*(\d+[\.\)]|[-*•])\s*", "", line)
        if line:
            cleaned.append(line)
    return cleaned or [raw.strip()]

async def evaluate_answers(project_title: str, project_description: str, project_outcomes: str, answers: List[dict]) -> EvaluationSummary:
    if not answers:
        return EvaluationSummary(
            overall_alignment="weak",
            alignment_score=0.0,
            narrative="No answers were provided during the Viva session.",
            outcome_evaluation=[]
        )
        
    system_prompt = (
        "You are an expert technical assessor grading a student's live viva session.\n"
        "The student has uploaded a project and was asked several questions about their code.\n"
        "Your job is to evaluate their answers based on correctness, technical depth, and alignment with the expected project outcomes.\n"
        "CRITICAL INSTRUCTIONS:\n"
        "1. DO NOT invent or hallucinate answers. Only grade what the student actually said in 'Student Answers'.\n"
        "2. If an answer is correct but brief, give it appropriate credit based on technical correctness.\n"
        "3. If an answer is incorrect, irrelevant, or hallucinates, score it strictly as 'not_met'.\n"
        "4. 'alignment_score' must reflect the true ratio of correct answers (0.0 to 1.0).\n\n"
        "Respond strictly with a JSON object matching this schema:\n"
        "{\n"
        '  "overall_alignment": "strong" | "partial" | "weak",\n'
        '  "alignment_score": float (0.0 to 1.0),\n'
        '  "narrative": "A professional 2-3 sentence summary of their verbal performance.",\n'
        '  "outcome_evaluation": [\n'
        '    {\n'
        '      "outcome": "Question or Skill assessed",\n'
        '      "status": "met" | "partial" | "not_met",\n'
        '      "evidence": "Short explanation of their answer",\n'
        '      "gap": "Optional gap if incorrect"\n'
        '    }\n'
        '  ]\n'
        "}\n"
    )
    
    user_prompt = f"Project Title: {project_title}\n"
    user_prompt += f"Project Description: {project_description}\n"
    user_prompt += f"Expected Outcomes:\n{project_outcomes}\n\n"
    user_prompt += "Student Answers:\n"
    for idx, ans in enumerate(answers):
        user_prompt += f"Q{idx+1}: {ans.get('question', '')}\n"
        user_prompt += f"A{idx+1}: {ans.get('answer', 'No answer provided')}\n\n"
        
    try:
        # Use async client so FastAPI's event loop is never blocked
        result, tokens_used = await call_json_async(system_prompt, user_prompt, max_tokens=1200)
        
        return EvaluationSummary(
            overall_alignment=result.get("overall_alignment", "weak"),
            alignment_score=result.get("alignment_score", 0.0),
            narrative=result.get("narrative", "Evaluation completed."),
            outcome_evaluation=[
                OutcomeEvaluation(
                    outcome=out.get("outcome", "Unknown"),
                    status=out.get("status", "not_met"),
                    evidence=out.get("evidence", "No evidence"),
                    gap=out.get("gap")
                ) for out in result.get("outcome_evaluation", [])
            ]
        )
    except Exception as e:
        print(f"Viva evaluation failed: {e}")
        return EvaluationSummary(
            overall_alignment="partial",
            alignment_score=0.5,
            narrative="Viva evaluation failed due to a processing error.",
            outcome_evaluation=[]
        )
