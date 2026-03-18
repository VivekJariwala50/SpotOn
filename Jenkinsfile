pipeline {
    agent any

    environment {
        DOCKER_IMAGE = "danaziz/smart-parking-app"
        EC2_HOST = "ubuntu@3.139.64.245"
    }

    stages {

       stage('Checkout') {
           steps {
               git branch: 'main', url: 'https://github.com/LZRVY/smart-parking-system.git'
           }
       }

        stage('Build & Push Image') {
            steps {
                sh '''
                docker buildx create --use || true
                docker buildx inspect --bootstrap

                docker buildx build \
                --platform linux/amd64 \
                --provenance=false \
                --no-cache \
                -t danaziz/smart-parking-app \
                --push .
                '''
            }
        }

        stage('Deploy to EC2') {
            steps {
                sh '''
                ssh -o StrictHostKeyChecking=no $EC2_HOST << EOF

                echo "=== CLEANING OLD CONTAINERS ==="
                docker stop app || true
                docker rm app || true
                
                echo "=== REMOVING OLD IMAGE ==="
                docker rmi -f danaziz/smart-parking-app || true
                
                echo "=== PULLING NEW IMAGE ==="
                docker pull --platform linux/amd64 danaziz/smart-parking-app
                
                echo "=== RUNNING CONTAINER ==="
                docker run -d -p 8000:8000 \
                --name app \
                -e DATABASE_URL=postgresql://admin:admin@172.31.41.214:5432/parking \
                danaziz/smart-parking-app
                
                echo "=== DEPLOYMENT DONE ==="
                
                EOF
                '''
            }
        }
    }
}
