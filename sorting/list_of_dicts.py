import os
import json
import csv

def load_reviews_from_directory(directory, container):
    files = os.listdir(directory)

    for file in files:
        if not file.endswith(".json"):
            continue

        file_path = os.path.join(directory, file)

        try:
            with open(file_path, "r") as f:
                data = json.load(f)

            if isinstance(data, dict):  
                container.append(data)

        except Exception as e:
            print(f"Error reading {file}: {e}")


def create_list_of_dicts_csv(output_csv):
    directories = [
        "assets/sorted/json/reviews/user_reviews/",
        "assets/sorted/json/reviews/sanitized_reviews/"
    ]

    list_of_dicts = []

    for directory in directories:
        if os.path.exists(directory):
            load_reviews_from_directory(directory, list_of_dicts)
        else:
            print(f"Directory not found: {directory}")

    if not list_of_dicts:
        print("No reviews found.")
        return

   
    all_keys = set()
    for review in list_of_dicts:
        all_keys.update(review.keys())

    fieldnames = sorted(all_keys)

    with open(output_csv, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(list_of_dicts)

    print(f"{output_csv} created with {len(list_of_dicts)} reviews.")


create_list_of_dicts_csv("listOfDicts.csv")