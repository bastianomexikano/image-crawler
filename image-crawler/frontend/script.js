document.getElementById('fetchButton').addEventListener('click', fetchData);

async function fetchData() {
    const searchTerm = document.getElementById('searchTerm').value.trim();
    const resultsDiv = document.getElementById('results');
    resultsDiv.innerHTML = 'Lade Daten...'; // Feedback for user

    if (!searchTerm) {
        resultsDiv.innerHTML = '<span class="error">Bitte einen Suchbegriff eingeben.</span>';
        return;
    }


    try {
        // Sends request to backend 
        const response = await fetch(`<span class="math-inline">\{apiUrl\}?term\=</span>{encodeURIComponent(searchTerm)}`, {
            method: 'GET', 
            headers: {
                'Content-Type': 'application/json'
            }
        });

        if (!response.ok) {
            // fetch error if there are any
            let errorMsg = `Fehler: ${response.status} ${response.statusText}`;
            try {
                const errorData = await response.json();
                errorMsg += ` - ${errorData.message || 'Keine weitere Information.'}`;
            } catch (e) { }
            throw new Error(errorMsg);
        }

        const data = await response.json(); // backend response

        // display data 
        if (data && data.images && data.images.length > 0) {
            resultsDiv.innerHTML = ''; // Ergebnisse löschen
            data.images.forEach(img => {
                const item = document.createElement('div');
                item.classList.add('image-item');
                item.innerHTML = `<img src="${img.url}" alt="Instagram Bild ${img.id || ''}"> <span>ID: <span class="math-inline">\{img\.id \|\| 'N/A'\} \| Link\: <a href\="</span>{img.permalink || '#'}" target="_blank">Post</a></span>`;
                resultsDiv.appendChild(item);
            });
        } else {
            resultsDiv.innerHTML = 'Keine Bilder gefunden oder ungültige Antwort.';
        }

    } catch (error) {
        console.error('Fehler beim Abrufen der Daten:', error);
        resultsDiv.innerHTML = `<span class="error">Ein Fehler ist aufgetreten: ${error.message}</span>`;
    }
}