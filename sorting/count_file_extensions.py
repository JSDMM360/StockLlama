import os
from collections import Counter
from find_all_extensions import find_all_extensions

def count_file_extensions(directory):
    file_extensions_set=find_all_extensions(directory)
    file_extension_counts=dict.fromkeys(file_extensions_set,0)
    print(file_extension_counts)
    files=os.listdir(directory)
    for file in files:
        file_extension_counts[file.split(".")[1]]=file_extension_counts[file.split(".")[1]]+1

    return file_extension_counts

#count_file_extensions("/home/jsdmm360/Documents/Repos/ADSA_project/assets/unsorted/")