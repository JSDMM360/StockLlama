import os
import json


def load_reviews_from_directory(directory, container):
    for file in os.listdir(directory):
        if not file.endswith(".json"):
            continue

        file_path = os.path.join(directory, file)

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, dict):
                container.append(data)

        except Exception as e:
            print(f"Error reading {file}: {e}")


def create_list_of_dicts_json(output_json):
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

    with open(output_json, "w", encoding="utf-8") as out_file:
        json.dump(list_of_dicts, out_file, indent=4)

    print(f"{output_json} created with {len(list_of_dicts)} reviews.")


create_list_of_dicts_json("listOfDicts.json")