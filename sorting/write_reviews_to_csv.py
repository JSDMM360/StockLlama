import csv
import os
import json
def write_reviews_to_csv(write_file_directory,source_directory):
    with open(write_file_directory,"a") as fil:
        for file in os.listdir(source_directory):
            with open(source_directory+file,'r') as ex:
                print(json.loads(ex.read()).values())
                val=json.loads(ex.read()).values()
                csv.writer(fil).writerow(val)
                exit(1)
