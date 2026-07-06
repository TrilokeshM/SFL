import asyncio
from app.skill_engine import suggest_skills, generate_questions
from app.zip_analyzer import CodeEvidence
from app.main import _load_catalog

def main():
    catalog = _load_catalog()
    evidence = CodeEvidence(
        files_analyzed=5,
        file_tree=["app/main.py", "app/database.py"],
        dependencies={"requirements.txt": "fastapi\nuvicorn\nsqlalchemy"},
        snippets={"app/main.py": "from fastapi import FastAPI\napp = FastAPI()"}
    )
    
    print("Testing suggest_skills...")
    skills, tokens = suggest_skills(evidence, catalog)
    print(f"Skills: {skills}")
    
    if skills:
        print("Testing generate_questions...")
        questions, tokens = generate_questions(evidence, skills, 2)
        print(f"Questions: {questions}")
    else:
        print("No skills returned, cannot test generate_questions")

if __name__ == "__main__":
    main()
