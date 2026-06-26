import os
from find_all_extensions import find_all_extensions

def create_folder_based_on_extension(search_directory,create_directory):
    file_extensions=find_all_extensions(search_directory)
    for ext in file_extensions:
        print(create_directory+"/"+ext)
        if not os.path.exists(create_directory+"/"+ext):
            print("hello")
            os.makedirs(create_directory+"/"+ext)
    


