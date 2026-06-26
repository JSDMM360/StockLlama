import shutil
import os

def move_file_into_corresponding_sorted_folder(source_directory,destination_directory):
    files=os.listdir(source_directory)
    for file in files:
        shutil.move(source_directory+file,destination_directory+file.split('.')[1])
    
