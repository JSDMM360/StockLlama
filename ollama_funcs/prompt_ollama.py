import ollama
from pydantic import BaseModel

""" class research_paper(BaseModel):
    title: str
    methodology:str
    model_used:str
    references:list[str]
    advantages: list[str]
    disadvantages:list[str]
    data_set:list[str]
    results: str
 """

class Prompt:
    respose: str
""" 
def prompt_ollama(prompt,jsondata):
    #response = ollama.generate(model='gemma2:27b', prompt=f"{prompt}{jsondata}",format=research_paper.model_json_schema(),options={'temperature': 0})
    response = ollama.generate(model='gemma2:2B', prompt=f"{prompt}{jsondata}",options={'temperature': 0.7},raw=False)
    return (response.response) """

def prompt_ollama(prompt):
    response = ollama.generate(model='TinyLlama', prompt=f"{prompt}")
    return (response.response)
#this is test code to check if the model is working or not


