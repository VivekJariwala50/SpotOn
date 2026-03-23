pipeline {
    agent any

    environment {
        DOCKER_IMAGE = "danaziz/smart-parking-app"
        EC2_HOST = "ubuntu@3.139.64.245"
        APP_URL = "http://3.139.64.245:8000"
    }

    stages {

        stage('Build & Push Image') {
            steps {
                sh '''
                docker buildx create --use || true
                docker buildx inspect --bootstrap

                docker buildx build \
                --platform linux/amd64 \
                --provenance=false \
                --no-cache \
                -t $DOCKER_IMAGE \
                --push .
                '''
            }
        }

        stage('Deploy to EC2') {
            steps {
                sh """
               ssh -o StrictHostKeyChecking=no ${EC2_HOST} '

                docker network create parking-net || true

                docker stop app || true
                docker rm app || true

                docker stop postgres-db || true
                docker rm postgres-db || true

                docker run -d --name postgres-db \
                --network parking-net \
                -e POSTGRES_USER=admin \
                -e POSTGRES_PASSWORD=admin \
                -e POSTGRES_DB=parking \
                -p 5432:5432 postgres

                sleep 10

                docker pull --platform linux/amd64 ${DOCKER_IMAGE}

                docker run -d -p 8000:8000 --name app \
                --network parking-net \
                -e DATABASE_URL=postgresql://admin:admin@postgres-db:5432/parking \
                ${DOCKER_IMAGE}

                '
                """
            }
        }

        stage('Verify Deployment') {
            steps {
                sh """
                sleep 15

                curl -f http://3.139.64.245:8000 || exit 1
                echo "✅ App is LIVE at:"
                echo "http://3.139.64.245:8000"
                """
            }
        }
    }

    post {
        success {
            echo "✅ App is LIVE at: ${APP_URL}"
        }
        failure {
            echo "❌ App is NOT reachable"
        }
    }
}
