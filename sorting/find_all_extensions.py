import os

def find_all_extensions(directory):
    file_extensions_set=set()
    files=os.listdir(directory)
    for file in files:
        if file.count(".")>=2:
            continue
        file_extensions_set.add((file.split(".")[1]))
    
    return file_extensions_set
